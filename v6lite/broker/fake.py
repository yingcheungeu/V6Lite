"""Deterministic in-memory broker for tests and offline rehearsal.

It can reproduce the IBKR behaviours that caused real V6 incidents:
duplicate execution callbacks, Cancelled -> Filled races, Submitted with
zero fill, late commission reports, disconnects and submit exceptions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from v6lite.broker.base import BrokerError
from v6lite.models import (
    Bar, BrokerAck, BrokerEvent, BrokerPosition, CommissionEvent, ConnectionEvent, FillEvent,
    FundsSnapshot, OpenOrder, OrderRequest, ProtectionRequest, Purpose, Quote, Side, StatusEvent,
)


@dataclass
class _Order:
    req: OrderRequest | ProtectionRequest
    broker_order_id: int
    status: str = "Submitted"
    filled: int = 0
    fills: list[FillEvent] = field(default_factory=list)

    @property
    def remaining(self) -> int:
        return self.req.quantity - self.filled


class FakeBroker:
    def __init__(self, account: str = "DU0000000", net_liq: float = 1_000_000.0, clock=None,
                 client_id: int = 131):
        self.account = account
        self.client_id = client_id
        self.accounts = [account]
        self.net_liq: float | None = net_liq
        self.clock = clock
        self.connected = False
        self._connection_syncing = False
        self.connect_snapshot_hook = None
        self.connection_snapshots: list[tuple[str, object]] = []
        self.heartbeat_requests = 0
        self.heartbeat_replies = 0
        self.heartbeat_server_times: list[datetime] = []
        self.heartbeat_auto_reply = True
        self.quotes: dict[str, Quote] = {}
        self.bars: dict[str, list[Bar]] = {}
        self.vix: float | None = 18.0
        self.orders: dict[str, _Order] = {}
        self.sent: list[OrderRequest] = []
        self.sent_protections: list[ProtectionRequest] = []
        self.modified_protections: list[ProtectionRequest] = []
        self.fail_next_protection: BrokerError | None = None
        self.fail_next_modify: BrokerError | None = None
        self.protection_auto_status = True
        self.protection_echo_lmt: object | None = 340.19000000000005
        self.protection_pending_modifications: dict[str, ProtectionRequest] = {}
        self.cancels: list[str] = []
        self.external_positions: dict[str, tuple[float, float]] = {}
        self.events: list[BrokerEvent] = []
        self.fail_next_submit: BrokerError | None = None
        self.fail_executions = False
        self.invisible_execs: set[str] = set()      # executions reqExecutions does not return (yet)
        self.stale_positions: dict[str, float] | None = None   # positions() cache lagging the truth
        self.fail_open_orders = False
        self.refuse_connect = False
        self.available: float | None = 1_000_000.0
        self.funds_as_of: datetime | None = None     # None -> "just updated"
        self.funds_seen_at: datetime | None = None   # None -> same as funds_as_of
        self.funds_ready = True
        # declared worst-case duration of one blocking call (seconds)
        self.costs: dict[str, float] = {"history": 10.0, "vix": 25.0, "query": 15.0}
        self.prev_close: dict[str, float] = {}
        self._next_id = 1000
        self._exec_seq = 0
        self.con_ids: dict[str, int] = {}

    # ---------------- helpers ----------------
    def _now(self) -> datetime:
        return self.clock.now() if self.clock else datetime.now(timezone.utc)

    def set_quote(self, symbol: str, *, bid: float | None, ask: float | None, last: float | None,
                  ts: datetime | None = None) -> None:
        self.quotes[symbol] = Quote(symbol, bid, ask, last, ts or self._now())

    # ---------------- BrokerPort ----------------
    def connect(self) -> None:
        if self.refuse_connect:
            raise BrokerError("gateway unreachable")
        self.connected = True
        self._connection_syncing = True
        try:
            # Every connection fixture models the server's automatic
            # same-client working-order snapshot. Local objects only:
            # no fresh query, write, End credit or coverage is supplied.
            for iid, order in self.orders.items():
                if order.status not in {"Submitted", "PreSubmitted", "PendingCancel"}:
                    continue
                snapshot = self._open_order_snapshot(iid, order)
                self._connect_snapshot("openOrder", snapshot)
                hint = StatusEvent(iid, order.status, order.filled, order.remaining,
                    order.broker_order_id, order.broker_order_id + 50_000,
                    broker_echo_lmt=snapshot.broker_echo_lmt, connection_snapshot=True)
                self.events.append(hint)
                self._connect_snapshot("orderStatus", hint)
            self._connect_snapshot("openOrderEnd", None)
        except Exception:
            self.connected = False
            raise
        finally:
            self._connection_syncing = False
        self._connect_snapshot("synchronized", None)

    def _connect_snapshot(self, stage, payload) -> None:
        self.connection_snapshots.append((stage, payload))
        if self.connect_snapshot_hook is not None:
            self.connect_snapshot_hook(stage, payload)

    def is_synchronized(self) -> bool:
        return self.connected and not self._connection_syncing

    def _require_synchronization(self) -> None:
        if self._connection_syncing:
            raise BrokerError("connection synchronization incomplete; fresh evidence unavailable")

    def disconnect(self) -> None:
        self.connected = False

    def is_connected(self) -> bool:
        return self.connected

    def pump(self, seconds: float) -> None:  # events are pushed synchronously by helpers
        return None

    def gateway_heartbeat_request(self) -> None:
        self.heartbeat_requests += 1
        if self.heartbeat_auto_reply:
            self.heartbeat_replies += 1
            self.heartbeat_server_times.append(self._now())

    def gateway_heartbeat_reply_count(self) -> int:
        return self.heartbeat_replies

    def gateway_heartbeat_replies(self) -> tuple[datetime, ...]:
        return tuple(self.heartbeat_server_times)

    def managed_accounts(self) -> list[str]:
        return list(self.accounts)

    def net_liquidation(self) -> float | None:
        return self.net_liq

    def market_data_status(self) -> dict:
        return {"requested": "live", "delayed_symbols": sorted(getattr(self, "delayed_symbols", [])),
                "no_subscription": sorted(getattr(self, "no_subscription", []))}

    def available_funds(self) -> FundsSnapshot:
        as_of = self.funds_as_of or self._now()
        return FundsSnapshot(self.available, as_of, self.funds_seen_at or as_of, self.funds_ready)

    def worst_case_seconds(self, kind: str) -> float:
        return self.costs[kind]

    def refresh_funds(self) -> None:
        """Single funds download, independent of the classic account stream."""
        self.funds_refreshes = getattr(self, "funds_refreshes", 0) + 1
        if getattr(self, "account_answers_refresh", False) and self.funds_seen_at is not None:
            self.funds_seen_at = self._now()  # simulated actual reply, not a request timestamp

    def refresh_account(self) -> None:
        """Like the real adapter: a non-blocking re-subscription. Nothing is
        fresher until IBKR pushes again (`account_answers_refresh` simulates
        an immediate re-send)."""
        self.account_refreshes = getattr(self, "account_refreshes", 0) + 1
        if getattr(self, "account_answers_refresh", False) and self.funds_seen_at is not None:
            self.funds_seen_at = self._now()

    def refresh_subscriptions(self) -> None:
        self.subscription_refreshes = getattr(self, "subscription_refreshes", 0) + 1

    def previous_close(self, symbol: str) -> float | None:
        if symbol in self.prev_close:
            return self.prev_close[symbol]
        q = self.quotes.get(symbol)
        return q.last if q else None

    def positions(self) -> list[BrokerPosition]:
        """The streamed position CACHE (can lag: see stale_positions)."""
        truth = self.positions_fresh()
        if self.stale_positions is None:
            return truth
        by = {p.symbol: p for p in truth}
        for sym, qty in self.stale_positions.items():
            by[sym] = BrokerPosition(sym, qty, by[sym].avg_cost if sym in by else 0.0)
        return [p for _, p in sorted(by.items()) if p.quantity != 0]

    def positions_fresh(self) -> list[BrokerPosition]:
        """A fresh server-side position query (reqPositions): the truth."""
        self._require_synchronization()
        book: dict[str, list[float]] = {s: [q, c] for s, (q, c) in self.external_positions.items()}
        for order in self.orders.values():
            for f in order.fills:
                qty, cost = book.get(f.symbol, [0.0, 0.0])
                if f.side is Side.BUY:
                    total = qty + f.quantity
                    cost = (cost * qty + f.price * f.quantity) / total if total else 0.0
                    qty = total
                else:
                    qty -= f.quantity
                book[f.symbol] = [qty, cost]
        return [BrokerPosition(s, q, c, self.account, self.con_ids.get(s))
                for s, (q, c) in sorted(book.items()) if q != 0]

    def _open_order_snapshot(self, iid, order) -> OpenOrder:
        req = order.req
        protect = isinstance(req, ProtectionRequest)
        return OpenOrder(iid, order.broker_order_id, req.symbol, order.status, order.filled, order.remaining,
            "SELL" if protect else str(req.side), iid.startswith("v6l-"),
            order.broker_order_id + 50_000, self.client_id, self.con_ids.get(req.symbol),
            req.order_type if protect else "LMT", "GTC" if protect else "DAY",
            req.trigger_price if protect else None, req.limit_price, False, self.account, req.quantity,
            self.protection_echo_lmt if protect and req.order_type == "STP" else None)

    def open_orders(self) -> list[OpenOrder]:
        self._require_synchronization()
        if self.fail_open_orders:
            raise BrokerError("open orders request timed out")
        return [self._open_order_snapshot(iid, order) for iid, order in self.orders.items()
                if order.status in {"Submitted", "PreSubmitted", "PendingCancel"}]

    def qualify(self, symbol: str) -> int:
        return self.con_ids.setdefault(symbol, 10_000 + len(self.con_ids))

    def subscribe_quotes(self, symbols: list[str]) -> None:
        return None

    def quote(self, symbol: str) -> Quote | None:
        return self.quotes.get(symbol)

    def daily_bars(self, symbol: str, days: int) -> list[Bar]:
        return list(self.bars.get(symbol, []))

    def vix_close(self) -> float | None:
        return self.vix

    def submit_limit(self, req: OrderRequest) -> BrokerAck:
        if req.purpose == Purpose.PROTECT or req.limit_price is None or req.limit_price <= 0:
            raise BrokerError("only non-PROTECT LMT orders allowed")
        if not self.connected:
            raise BrokerError("not connected")
        if self.fail_next_submit is not None:
            err, self.fail_next_submit = self.fail_next_submit, None
            if err.maybe_sent:
                self._register(req)  # the order did reach the "exchange"
            raise err
        order = self._register(req)
        return BrokerAck(order.broker_order_id, order.broker_order_id + 50_000)

    def submit_stop(self, req: ProtectionRequest) -> BrokerAck:
        if req.order_type != "STP" or req.limit_price is not None:
            raise BrokerError("protection must be STP without limit")
        if not self.connected:
            raise BrokerError("not connected")
        if self.fail_next_protection is not None:
            err, self.fail_next_protection = self.fail_next_protection, None
            if err.maybe_sent:
                self._register(req)
            raise err
        order = self._register(req)
        order.status = "PendingSubmit"
        self.sent_protections.append(req)
        if self.protection_auto_status:
            self.status(req.intent_id, "Submitted")
        return BrokerAck(order.broker_order_id, order.broker_order_id + 50_000)

    def modify_protection(self, req: ProtectionRequest) -> BrokerAck:
        if req.order_type != "STP" or req.limit_price is not None:
            raise BrokerError("protection must be STP without limit")
        if not self.connected:
            raise BrokerError("not connected")
        old = self.orders.get(req.intent_id)
        if old is None or not isinstance(old.req, ProtectionRequest):
            raise BrokerError("protection order not found")
        if self.fail_next_modify is not None:
            err, self.fail_next_modify = self.fail_next_modify, None
            raise err
        self.modified_protections.append(req)
        self.protection_pending_modifications[req.intent_id] = req
        if self.protection_auto_status:
            self.confirm_protection_modify(req.intent_id)
        return BrokerAck(old.broker_order_id, old.broker_order_id + 50_000)

    def confirm_protection_modify(self, intent_id: str) -> None:
        req = self.protection_pending_modifications.pop(intent_id)
        order = self.orders[intent_id]
        order.req = req
        self.status(intent_id, "Submitted")

    def _register(self, req: OrderRequest | ProtectionRequest) -> _Order:
        self._next_id += 1
        order = _Order(req, self._next_id)
        self.orders[req.intent_id] = order
        if isinstance(req, OrderRequest):
            self.sent.append(req)
        return order

    def cancel(self, intent_id: str, *, expected_order_id: int | None = None,
               expected_perm_id: int | None = None, expected_con_id: int | None = None,
               allow_lookup: bool = True) -> None:
        order = self.orders.get(intent_id)
        if expected_order_id is not None and (order is None or order.broker_order_id != expected_order_id or
                order.broker_order_id + 50_000 != expected_perm_id or
                self.con_ids.get(order.req.symbol) != expected_con_id):
            raise BrokerError("protection cancel identity mismatch")
        self.cancels.append(intent_id)

    def executions_today(self) -> list[FillEvent]:
        self._require_synchronization()
        if self.fail_executions:
            raise BrokerError("executions unavailable")
        return [f for o in self.orders.values() for f in o.fills if f.exec_id not in self.invisible_execs]

    def account_executions_today(self) -> list[FillEvent]:
        """All scripted account executions, independent of a client-scoped override."""
        self._require_synchronization()
        if self.fail_executions:
            raise BrokerError("account executions unavailable")
        return [FillEvent(**{**f.__dict__, "intent_id": ""})
                for o in self.orders.values() for f in o.fills
                if f.exec_id not in self.invisible_execs]

    def drain_events(self) -> list[BrokerEvent]:
        out, self.events = self.events, []
        return out

    # ---------------- scripted broker behaviour ----------------
    def fill(self, intent_id: str, qty: int | None = None, price: float | None = None, *,
             commission: float | None = 1.0, commission_separately: bool = False,
             emit: bool = True, duplicate: bool = False, exec_id: str | None = None) -> FillEvent:
        order = self.orders[intent_id]
        qty = order.remaining if qty is None else qty
        if qty <= 0 or qty > order.remaining:
            raise ValueError("bad fill qty")
        self._exec_seq += 1
        fill = FillEvent(
            exec_id=exec_id or f"0000fa4e.{self._exec_seq:08x}.01.01", intent_id=intent_id,
            symbol=order.req.symbol,
            side=Side.SELL if isinstance(order.req, ProtectionRequest) else order.req.side,
            quantity=qty, price=(order.req.trigger_price if isinstance(order.req, ProtectionRequest) else order.req.limit_price) if price is None else price,
            time=self._now(), broker_order_id=order.broker_order_id,
            perm_id=order.broker_order_id + 50_000, con_id=self.con_ids.get(order.req.symbol),
            commission=None if commission_separately else commission)
        order.fills.append(fill)
        order.filled += qty
        if order.status not in {"Cancelled", "ApiCancelled", "Inactive"}:  # late fills keep the cancel
            order.status = "Filled" if order.remaining == 0 else "Submitted"
        if emit:
            self.events.append(fill)
            if duplicate:
                self.events.append(fill)
            if commission_separately and commission is not None:
                self.events.append(CommissionEvent(fill.exec_id, commission))
            self.events.append(StatusEvent(intent_id, order.status, order.filled, order.remaining,
                                           order.broker_order_id))
        return fill

    def status(self, intent_id: str, raw: str) -> None:
        order = self.orders[intent_id]
        order.status = raw
        self.events.append(StatusEvent(intent_id, raw, order.filled, order.remaining,
                                       order.broker_order_id, order.broker_order_id + 50_000))

    def confirm_cancel(self, intent_id: str) -> None:
        self.status(intent_id, "Cancelled")

    def drop_connection(self) -> None:
        self.connected = False
        self.events.append(ConnectionEvent(False, "fake_drop"))

    def external_fill(self, symbol: str, side: Side, qty: int, price: float) -> FillEvent:
        """A fill V6-Lite did not originate (manual TWS trade on this client)."""
        self._exec_seq += 1
        fill = FillEvent(f"0000e47e.{self._exec_seq:08x}.01.01", "", symbol, side, qty, price, self._now())
        self.events.append(fill)
        q, c = self.external_positions.get(symbol, (0.0, 0.0))
        self.external_positions[symbol] = (q + qty if side is Side.BUY else q - qty, price)
        return fill
