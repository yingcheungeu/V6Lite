"""Domain models shared by strategy, engine, store and broker adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class Purpose(StrEnum):
    ENTRY = "ENTRY"
    STOP = "STOP"
    PROTECT = "PROTECT"
    TP1 = "TP1"
    TP2 = "TP2"
    TP3 = "TP3"
    SHUTDOWN = "SHUTDOWN"  # reserved; V6-Lite never flattens automatically


class IntentStatus(StrEnum):
    NEW = "NEW"                        # persisted, not yet handed to broker
    SENT = "SENT"                      # broker accepted the call
    PARTIAL = "PARTIAL"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCEL_SEEN = "CANCEL_SEEN"        # broker said cancelled; waiting grace + executions
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"            # terminal (may carry a partial fill)
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"                # send outcome not provable -> blocks entries
    DRYRUN = "DRYRUN"


ACTIVE_STATUSES = frozenset({
    IntentStatus.NEW, IntentStatus.SENT, IntentStatus.PARTIAL,
    IntentStatus.CANCEL_REQUESTED, IntentStatus.CANCEL_SEEN, IntentStatus.UNKNOWN,
})
TERMINAL_STATUSES = frozenset({
    IntentStatus.FILLED, IntentStatus.CANCELLED, IntentStatus.REJECTED, IntentStatus.DRYRUN,
})


@dataclass(frozen=True)
class Bar:
    day: str
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class Quote:
    symbol: str
    bid: float | None
    ask: float | None
    last: float | None
    ts: datetime | None
    # receive time of `last` when it is known separately from the book's
    # time (`ts`); the age of a mark priced from `last` is judged from it
    last_ts: datetime | None = None
    market_data_type: int | None = None

    def valid_bid_ask(self) -> bool:
        return (
            self.bid is not None and self.ask is not None
            and self.bid > 0 and self.ask > 0 and self.ask >= self.bid
        )

    def mid(self) -> float | None:
        if self.valid_bid_ask():
            return (self.bid + self.ask) / 2.0
        return None

    def spread_pct(self) -> float | None:
        mid = self.mid()
        if not mid:
            return None
        return (self.ask - self.bid) / mid

    def mark(self) -> float | None:
        """Best price for valuation: last trade, else midpoint."""
        if self.last is not None and self.last > 0:
            return self.last
        return self.mid()

    def age_seconds(self, now: datetime) -> float | None:
        if self.ts is None:
            return None
        return max(0.0, (now - self.ts).total_seconds())

    def mark_age_seconds(self, now: datetime) -> float | None:
        """Age of the data mark() is priced from (last's own time if known)."""
        if self.last is not None and self.last > 0 and self.last_ts is not None:
            return max(0.0, (now - self.last_ts).total_seconds())
        return self.age_seconds(now)


@dataclass(frozen=True)
class Signal:
    symbol: str
    score: int
    price: float
    atr: float
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class OrderRequest:
    intent_id: str
    symbol: str
    side: Side
    quantity: int
    limit_price: float | None
    purpose: Purpose
    position_id: str | None = None
    wire_deadline: datetime | None = None  # TP only: must send immediately before this quote expires


@dataclass(frozen=True)
class ProtectionRequest:
    intent_id: str
    position_id: str
    symbol: str
    quantity: int
    trigger_price: float
    limit_price: float | None = None
    broker_order_id: int | None = None
    perm_id: int | None = None
    con_id: int | None = None
    client_id: int | None = None
    account: str = ""
    allow_lookup: bool = True
    order_type: str = "STP"


@dataclass(frozen=True)
class BrokerAck:
    broker_order_id: int | None
    perm_id: int | None = None


@dataclass(frozen=True)
class BrokerPosition:
    symbol: str
    quantity: float
    avg_cost: float
    account: str = ""
    con_id: int | None = None


@dataclass(frozen=True)
class FundsSnapshot:
    """AvailableFunds as the broker last reported it.

    as_of   -- when the value last CHANGED (fills after this moment are not
               reflected in it and must be reserved by the caller)
    seen_at -- when IBKR last pushed AvailableFunds itself (freshness)
    ready   -- False when IBKR flags the account values as not ready
    """
    value: float | None
    as_of: datetime | None
    seen_at: datetime | None
    ready: bool = True


@dataclass(frozen=True)
class OpenOrder:
    intent_id: str             # orderRef for V6-Lite orders; "foreign:<permId|orderId>" otherwise
    broker_order_id: int | None
    symbol: str
    status: str
    filled: float
    remaining: float
    side: str = ""             # "BUY" / "SELL"; "" = unknown (treated as a SELL for share reservation)
    ours: bool = True          # False: a working order V6-Lite did not create (manual, other system)
    perm_id: int | None = None
    client_id: int | None = None
    con_id: int | None = None
    order_type: str = ""
    tif: str = ""
    aux_price: float | None = None
    limit_price: float | None = None
    outside_rth: bool | None = None
    account: str = ""
    total_quantity: int | None = None
    broker_echo_lmt: object | None = None  # raw STP diagnostic only, never contract pricing


@dataclass(frozen=True)
class CompletedOrder:
    """Raw completed callback evidence; absent native IDs remain absent."""
    account: str
    con_id: int | None
    broker_order_id: int | None
    perm_id: int | None
    intent_id: str
    symbol: str
    status: str
    filled: int | None
    total_quantity: int | None
    order_type: str
    action: str
    sec_type: str
    currency: str
    client_id: int | None
    tif: str
    aux_price: object | None  # exact native Decimal, never a rounded float
    outside_rth: bool | None
    completed_time: datetime | None
    completed_time_raw: str
    raw_signature: tuple[tuple[str, str], ...]
    native_order_id_absent: bool
    native_client_id_absent: bool


@dataclass(frozen=True)
class CompletedOrdersSnapshot:
    orders: tuple[CompletedOrder, ...]
    query_started_at: datetime
    completed_at: datetime
    socket_generation: int
    query_request_id: int


@dataclass(frozen=True)
class CompletedCancelProof:
    snapshot: CompletedOrdersSnapshot
    order: CompletedOrder
    protection: tuple[tuple[str, object], ...]
    intent: tuple[tuple[str, object], ...]
    position: tuple[tuple[str, object], ...]
    booked_filled: int
    duplicate_rows: int
    matched_values: str


# ---------------- broker -> engine events ----------------
@dataclass(frozen=True)
class FillEvent:
    exec_id: str
    intent_id: str          # IBKR orderRef; "" for fills we did not originate
    symbol: str
    side: Side
    quantity: int
    price: float
    time: datetime
    broker_order_id: int | None = None
    perm_id: int | None = None
    commission: float | None = None
    time_source: str = ""
    time_uncertain: bool = False
    con_id: int | None = None
    raw_time: str | None = None


@dataclass(frozen=True)
class CommissionEvent:
    exec_id: str
    commission: float


@dataclass(frozen=True)
class StatusEvent:
    intent_id: str
    raw_status: str
    filled: float
    remaining: float
    broker_order_id: int | None = None
    perm_id: int | None = None
    message: str = ""
    order_identity: OpenOrder | None = None
    error_code: int = 0
    identity_checked: bool = False
    terminal_identity: OpenOrder | None = None  # diagnostic only, never an order authority
    mkt_cap_price: float | None = None
    broker_echo_lmt: object | None = None  # preserves an incomplete ApiPending echo
    connection_snapshot: bool = False  # creation-time role; hint/audit, never fresh authority
    observed_at: datetime | None = None
    socket_generation: int | None = None
    query_snapshot: bool = False  # creation-time role for a native own-query status


@dataclass(frozen=True)
class PriceObservationEvent:
    identity: OpenOrder       # cannot adopt IDs; only a bound own price ACK can confirm pending
    source: str
    mkt_cap_price: float | None = None
    mkt_cap_price_raw: str = ""
    error_code: int = 0
    reason: str = ""
    connection_snapshot: bool = False  # remains a snapshot after sync has completed
    observed_at: datetime | None = None  # fixed at creation, never drain time
    socket_generation: int | None = None
    query_snapshot: bool = False  # individual query rows never acknowledge a pending write
    owned_open_orders: tuple[OpenOrder, ...] | None = None  # only after our native openOrderEnd
    query_started_at: datetime | None = None
    query_request_id: int | None = None


@dataclass(frozen=True)
class OrderBookChangeEvent:
    symbol: str  # invalidation only; never absence, identity or SELL authority
    intent_id: str = ""
    contract_echo: tuple | None = None  # immutable native openOrder contract, no remaining authority
    observed_at: datetime | None = None
    socket_generation: int | None = None
    connection_snapshot: bool = False
    query_snapshot: bool = False


@dataclass(frozen=True)
class OpenOrdersNormalizationEvent:
    """Completed-query diagnostics; duplicate rows never grant authority."""
    order_ids: tuple[int, ...]
    perm_ids: tuple[int, ...]
    intent_ids: tuple[str, ...]
    raw_rows: int
    duplicate_rows: int
    conflicting_fields: tuple[str, ...]
    observed_at: datetime
    socket_generation: int
    query_request_id: int


@dataclass(frozen=True)
class ConnectionEvent:
    connected: bool
    reason: str = ""


@dataclass(frozen=True)
class BrokerNoticeEvent:
    """Raw informational broker text; audit only, without trading authority."""
    req_id: int
    error_code: int
    raw_message: str


@dataclass(frozen=True)
class AnomalyEvent:
    """Broker data V6-Lite refuses to interpret (e.g. fractional shares)."""
    code: str
    detail: str


@dataclass(frozen=True)
class ExecutionTimeDiagnosticEvent:
    """Time-field observation only: no economic fill or order authority."""
    exec_id: str
    source: str
    raw_time: str | None
    category: str
    invalid: bool = False


class ExecutionQueryResult(list):
    """List-compatible rows with this native request's explicit day scope."""
    def __init__(self, rows, scope):
        super().__init__(rows)
        self.scope = dict(scope)


BrokerEvent = OpenOrdersNormalizationEvent | OrderBookChangeEvent | FillEvent | CommissionEvent | StatusEvent | PriceObservationEvent | ConnectionEvent | AnomalyEvent | BrokerNoticeEvent | ExecutionTimeDiagnosticEvent


@dataclass
class Decision:
    allowed: bool
    reasons: list[str] = field(default_factory=list)

    def block(self, reason: str) -> "Decision":
        self.allowed = False
        self.reasons.append(reason)
        return self
