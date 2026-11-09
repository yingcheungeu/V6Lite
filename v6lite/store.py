"""Durable state in one SQLite file (WAL, transactional).

Why SQLite instead of JSON ledgers: the safety invariants V6 enforced with
large amounts of hand-written locking become database constraints here:

* fills.exec_id PRIMARY KEY        -> a fill is applied exactly once
* exit_stages PK(position, stage)  -> each TP/STOP stage starts exactly once
* one OPEN position per symbol     -> partial unique index
* intent row written before send   -> durable intent ledger (restart-safe)
* fill + position update in one transaction -> no half-applied fills
* IBKR execution corrections (same execId stem, new last segment) are
  quarantined, never booked as a second fill
* v1 runs only on FRESH state of this schema: older state files are refused,
  never migrated (their recovery semantics no longer exist)
* a position whose quantity cannot be explained is frozen; the operator
  flattens it and `release` closes it (RELEASED, never a trade) together
  with the day's entry freeze, in one transaction
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator

from v6lite.exits import Levels, PositionView
from v6lite.market_calendar import trading_day
from v6lite.models import (
    ACTIVE_STATUSES, FillEvent, IntentStatus, OrderRequest, Purpose, Side,
)

SCHEMA_VERSION = "4"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS intents (
  intent_id TEXT PRIMARY KEY,
  symbol TEXT NOT NULL,
  side TEXT NOT NULL,
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  limit_price REAL CHECK ((purpose='PROTECT' AND limit_price IS NULL) OR (limit_price IS NOT NULL AND limit_price > 0)),
  purpose TEXT NOT NULL,
  position_id TEXT,
  atr REAL,
  status TEXT NOT NULL,
  broker_order_id INTEGER,
  perm_id INTEGER,
  last_raw_status TEXT,
  created_at TEXT NOT NULL,
  sent_at TEXT,
  cancel_requested_at TEXT,
  cancel_seen_at TEXT,
  updated_at TEXT NOT NULL,
  note TEXT,
  hold_reason TEXT,            -- set: only the operator may resolve this intent
  recovery_position_id TEXT    -- ENTRY fills that arrive after its position closed
);
CREATE INDEX IF NOT EXISTS intents_status ON intents(status);
CREATE TABLE IF NOT EXISTS protections (
  position_id TEXT PRIMARY KEY,
  intent_id TEXT NOT NULL UNIQUE,
  symbol TEXT NOT NULL,
  account TEXT,
  client_id INTEGER,
  con_id INTEGER,
  trigger_price REAL NOT NULL,
  limit_price REAL,
  order_type TEXT NOT NULL DEFAULT 'STP LMT',
  requested_qty INTEGER NOT NULL DEFAULT 0,
  confirmed_remaining INTEGER NOT NULL DEFAULT 0,
  pending_max_remaining INTEGER NOT NULL DEFAULT 0,
  pending_qty INTEGER,
  pending_trigger REAL,
  pending_limit REAL,
  modify_generation INTEGER NOT NULL DEFAULT 0,
  status_seen_generation INTEGER NOT NULL DEFAULT -1,
  state TEXT NOT NULL,
  broker_order_id INTEGER,
  perm_id INTEGER,
  confirmed_at TEXT,
  gap_a_started_at TEXT,
  gap_b_started_at TEXT,
  tp_stage TEXT,
  tp_qty INTEGER,
  tp_intent_id TEXT,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS protections_intent ON protections(intent_id);
-- One local receive-time A window per booked BUY execution.  This additive
-- table is created for existing schema-4 state files without changing their
-- order/recovery identity; a pre-upgrade open gap remains as legacy evidence.
CREATE TABLE IF NOT EXISTS protection_gap_a (
  exec_id TEXT PRIMARY KEY,
  position_id TEXT NOT NULL,
  started_at TEXT NOT NULL,
  closed_at TEXT
);
CREATE INDEX IF NOT EXISTS protection_gap_a_position ON protection_gap_a(position_id, closed_at);
CREATE TABLE IF NOT EXISTS fills (
  exec_id TEXT PRIMARY KEY,
  intent_id TEXT,
  position_id TEXT,
  symbol TEXT NOT NULL,
  side TEXT NOT NULL,
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  price REAL NOT NULL CHECK (price > 0),
  time TEXT NOT NULL,
  trading_day TEXT NOT NULL,
  broker_order_id INTEGER,
  perm_id INTEGER,
  realized_pnl REAL NOT NULL DEFAULT 0,
  exec_base TEXT
);
CREATE TABLE IF NOT EXISTS quarantined_fills (
  exec_id TEXT PRIMARY KEY,
  exec_base TEXT NOT NULL,
  payload TEXT NOT NULL,
  recorded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS releases (
  release_id TEXT PRIMARY KEY,
  symbol TEXT NOT NULL,
  at TEXT NOT NULL,
  position_id TEXT NOT NULL,
  ledger_qty INTEGER NOT NULL,
  operator TEXT NOT NULL,
  reason TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS commissions (
  exec_id TEXT PRIMARY KEY,
  commission REAL NOT NULL,
  recorded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS positions (
  position_id TEXT PRIMARY KEY,
  symbol TEXT NOT NULL,
  status TEXT NOT NULL,
  source TEXT NOT NULL,
  entry_price REAL NOT NULL,
  atr REAL NOT NULL,
  qty_initial INTEGER NOT NULL,
  qty_open INTEGER NOT NULL CHECK (qty_open >= 0),
  stop REAL NOT NULL, tp1 REAL NOT NULL, tp2 REAL NOT NULL, tp3 REAL NOT NULL,
  breakeven_stop REAL,
  tp1_done INTEGER NOT NULL DEFAULT 0,
  tp2_done INTEGER NOT NULL DEFAULT 0,
  realized_pnl REAL NOT NULL DEFAULT 0,
  opened_at TEXT NOT NULL,
  closed_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS one_open_position_per_symbol
  ON positions(symbol) WHERE status = 'OPEN';
CREATE TABLE IF NOT EXISTS exit_stages (
  position_id TEXT NOT NULL,
  stage TEXT NOT NULL,
  status TEXT NOT NULL,
  target_qty INTEGER NOT NULL,
  filled_qty INTEGER NOT NULL DEFAULT 0,
  attempts INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (position_id, stage)
);
CREATE TABLE IF NOT EXISTS risk_days (
  trading_day TEXT PRIMARY KEY,
  sod_equity REAL NOT NULL,
  sod_unrealized REAL,
  entries_submitted INTEGER NOT NULL DEFAULT 0,
  daily_stop_latched INTEGER NOT NULL DEFAULT 0,
  latched_at TEXT,
  latched_reason TEXT
);
CREATE TABLE IF NOT EXISTS controls (
  key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL
);
"""


class StoreError(RuntimeError):
    pass


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise StoreError("naive datetime")
    return dt.astimezone(timezone.utc).isoformat()


def _parse(text: str | None) -> datetime | None:
    return datetime.fromisoformat(text) if text else None


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:20]}"


def exec_base(exec_id: str) -> str:
    """IBKR execIds look like 0000e0d5.65f3a8b2.01.01; a correction keeps
    everything up to the last '.' and changes the final number. Ids that do
    not have that shape are their own base (no correction detection)."""
    if exec_id.count(".") >= 3:
        stem, _, last = exec_id.rpartition(".")
        if last.isdigit():
            return stem
    return exec_id


@dataclass(frozen=True)
class FillOutcome:
    inserted: bool
    closed_position_id: str | None = None
    anomaly: str | None = None
    commission_added: bool = False


def _peek_schema(path: Path) -> str | None:
    """schema_version of an existing state file, read without writing to
    it (no table creation, no journal-mode change). None = new/empty file.
    A file that is not a readable V6-Lite state is refused."""
    if not path.exists() or path.stat().st_size == 0:
        return None
    try:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'").fetchone() is None:
                tables = conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0]
                if tables:
                    raise StoreError(f"{path} is not a V6-Lite state file (no meta table); nothing was changed")
                return None
            row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if row is None and conn.execute("SELECT COUNT(*) FROM meta").fetchone()[0] == 0:
                return None             # created but never initialised (first start interrupted)
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise StoreError(f"{path} cannot be read as a V6-Lite state file ({exc}); nothing was changed") from exc
    if row is None:
        raise StoreError(f"{path} has no schema_version; it is not a state file this version can use")
    return row[0]


class Store:
    def __init__(self, path: str | Path, levels_fn: Callable[[float, float], Levels]):
        from v6lite.observations import ObservationAudit
        self._observations = ObservationAudit()
        self._observation_pending = None
        self._observations_closed = False
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._levels_fn = levels_fn
        existing = _peek_schema(self.path)          # read-only: an older file is refused untouched
        if existing is not None and existing != SCHEMA_VERSION:
            raise StoreError(
                f"{self.path} is a V6-Lite state file of schema {existing}; this version runs only on fresh "
                f"state (schema {SCHEMA_VERSION}). Archive the old state directory and start fresh -- nothing "
                "was changed.")
        self.conn = sqlite3.connect(str(self.path), isolation_level=None, timeout=10.0)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        try:
            self.conn.executescript(_SCHEMA)
            self._upgrade_stp_layout()
        except BaseException:
            self.conn.close()
            raise
        self.conn.execute("CREATE INDEX IF NOT EXISTS fills_exec_base ON fills(exec_base)")
        if existing is None:
            self.set_meta("schema_version", SCHEMA_VERSION)

    def _upgrade_stp_layout(self) -> None:
        """Atomic schema-4 layout addition; old order values are never converted.

        Existing contracts acquire honest STP LMT metadata. Both nullable
        columns are rebuilt in one transaction, preserving every other column,
        constraint, explicit index and trigger from the original CREATE SQL.
        """
        with self.tx():
            columns = {r["name"] for r in self.conn.execute("PRAGMA table_info(protections)")}
            if "order_type" not in columns:
                self.conn.execute("ALTER TABLE protections ADD COLUMN order_type TEXT NOT NULL DEFAULT 'STP LMT'")
            for table in ("intents", "protections"):
                info = list(self.conn.execute(f"PRAGMA table_info({table})"))
                if not next(r["notnull"] for r in info if r["name"] == "limit_price"):
                    continue
                sql = self.conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()[0]
                objects = list(self.conn.execute("SELECT sql FROM sqlite_master WHERE tbl_name=? AND type IN ('index','trigger') AND sql IS NOT NULL", (table,)))
                # Accept only the known REAL NOT NULL column; unexpected layouts
                # fail closed and roll back BOTH tables rather than guessing.
                pattern = r'(?i)(["`]?limit_price["`]?\s+REAL)\s+NOT\s+NULL(?:\s+CHECK\s*\(\s*limit_price\s*>\s*0\s*\))?'
                definition = ("limit_price REAL CHECK ((purpose='PROTECT' AND limit_price IS NULL) OR "
                              "(limit_price IS NOT NULL AND limit_price > 0))" if table == "intents" else "limit_price REAL")
                sql, count = re.subn(pattern, definition, sql)
                if count != 1:
                    raise StoreError(f"unrecognised {table} limit schema; STP layout not changed")
                temp = "__b22_" + table
                sql, count = re.subn(r'(?i)^CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?["`]?'+table+r'["`]?', 'CREATE TABLE '+temp, sql, count=1)
                if count != 1:
                    raise StoreError("unrecognised CREATE table; STP layout not changed")
                self.conn.execute(sql)
                names = ','.join('"'+r['name']+'"' for r in info)
                self.conn.execute(f"INSERT INTO {temp}({names}) SELECT {names} FROM {table}")
                self.conn.execute(f"DROP TABLE {table}")
                self.conn.execute(f"ALTER TABLE {temp} RENAME TO {table}")
                for obj in objects:
                    self.conn.execute(obj["sql"])

    def close(self) -> None:
        if not self._observations_closed:
            self.flush_observations(self._observation_clock(), reason="store_close")
            self.conn.close()
            self._observations_closed = True

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        self.conn.execute("BEGIN IMMEDIATE")
        self._observation_pending = None
        try:
            yield self.conn
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        else:
            self.conn.execute("COMMIT")
            if self._observation_pending is not None:
                self._observations = self._observation_pending
        finally:
            self._observation_pending = None

    # ---------------- meta / binding ----------------
    def meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, value))

    def bind_account(self, account: str, mode: str) -> None:
        self.bind_identity(account, mode)

    def bind_identity(self, account: str, mode: str, *, role: str = "formal",
                      client_id: int | None = None) -> dict:
        """A state file belongs to exactly one account + mode + role forever.
        role 'formal' = regular order state; 'test' = isolated Paper test
        order state; 'dryrun' = read-only rehearsal state.
        Returns the previously bound identity (client_id may differ; the
        engine decides whether that is safe)."""
        with self.tx():
            bound = {k: self.meta(k) for k in ("account", "mode", "role", "client_id")}
            if bound["account"] is None:
                self.set_meta("account", account)
                self.set_meta("mode", mode)
                self.set_meta("role", role)
                if client_id is not None:
                    self.set_meta("client_id", str(client_id))
                return bound
            if bound["account"] != account or bound["mode"] != mode:
                raise StoreError(
                    f"state bound to account={bound['account']} mode={bound['mode']}; "
                    f"refusing account={account} mode={mode}")
            if (bound["role"] or "formal") != role:
                raise StoreError(f"state role is {bound['role'] or 'formal'}; refusing role={role}")
            if bound["role"] is None:
                self.set_meta("role", role)
            if bound["client_id"] is None and client_id is not None:
                self.set_meta("client_id", str(client_id))
            return bound

    def identity(self) -> dict:
        return {k: self.meta(k) for k in ("account", "mode", "role", "client_id")}

    # ---------------- audit / controls ----------------
    def audit(self, now: datetime, kind: str, **payload) -> None:
        self.conn.execute("INSERT INTO audit(ts, kind, payload) VALUES (?, ?, ?)",
                          (_iso(now), kind, json.dumps(payload, default=str, sort_keys=True)))

    def _observation_clock(self):
        clock = getattr(self, "report_clock", None)
        return clock.now() if clock is not None else (self._observations.latest or datetime.now(timezone.utc))

    def _observation_operation(self, operation):
        if not self.conn.in_transaction:
            # Multiple summary INSERTs form one observer-only atomic unit.
            # Reuse the existing tx; an economic caller's outer tx is never nested.
            with self.tx():
                self._observation_operation(operation)
            return
        # This is observer-only RAM staging, not a new/nested SQL transaction.
        # Existing outer tx can observe its pending state; failed writes or a
        # failed COMMIT never publish that candidate to the committed cache.
        base = self._observation_pending if self._observation_pending is not None else self._observations
        candidate = base.clone()
        operation(candidate)
        if self.conn.in_transaction:
            self._observation_pending = candidate
        else:
            self._observations = candidate

    def observation_audit(self, now, kind, *, generation=None, changed=None, **payload):
        self._observation_operation(lambda state: state.observe(
            self.audit, now, kind, payload, generation=generation, changed=changed))

    def observation_generation(self, now, generation):
        self._observation_operation(lambda state: state.change_generation(self.audit, now, generation))

    def flush_observations(self, now, *, reason="shutdown"):
        self._observation_operation(lambda state: state.flush(self.audit, now, reason=reason))

    def flush_due_observations(self, now):
        # Usually no window is due. Avoid cloning the cache on every tick.
        state = self._observation_pending if self._observation_pending is not None else self._observations
        if state.due(now):
            self._observation_operation(lambda candidate: candidate.flush_due(self.audit, now))

    def control(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM controls WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def set_control(self, now: datetime, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO controls(key, value, updated_at) VALUES (?, ?, ?)",
            (key, value, _iso(now)))

    def delete_control(self, key: str) -> None:
        self.conn.execute("DELETE FROM controls WHERE key=?", (key,))

    def controls_with_prefix(self, prefix: str) -> dict[str, str]:
        rows = self.conn.execute("SELECT key, value FROM controls WHERE key LIKE ?",
                                 (prefix + "%",)).fetchall()
        return {r["key"]: r["value"] for r in rows}

    # kill switch -------------------------------------------------------
    def kill(self, now: datetime, reason: str) -> None:
        with self.tx():
            self.set_control(now, "kill", json.dumps({"reason": reason, "at": _iso(now)}))
            self.audit(now, "KILL", reason=reason)

    def kill_state(self) -> dict | None:
        raw = self.control("kill")
        return json.loads(raw) if raw else None

    def resume(self, now: datetime, reason: str, operator: str) -> None:
        if not reason.strip() or not operator.strip():
            raise StoreError("resume requires operator and reason")
        with self.tx():
            day = self.risk_day(trading_day(now).isoformat())
            if day and day["daily_stop_latched"]:
                raise StoreError("daily loss stop is latched for today; cannot resume until next trading day")
            self.delete_control("kill")
            self.audit(now, "RESUME", reason=reason, operator=operator)

    # anomalies (latched until an operator clears them) -------------------
    def raise_anomaly(self, now: datetime, code: str, detail: str) -> None:
        self.set_control(now, f"anomaly:{code}", detail)
        self.audit(now, "ANOMALY", code=code, detail=detail)

    def anomalies(self) -> dict[str, str]:
        return self.controls_with_prefix("anomaly:")

    def clear_anomaly(self, now: datetime, code: str, operator: str, reason: str) -> None:
        with self.tx():
            key = code if code.startswith("anomaly:") else f"anomaly:{code}"
            if key.startswith("anomaly:frozen:"):
                raise StoreError(f"{code} is a frozen symbol: flatten it in TWS and run `release "
                                 f"{key.split(':', 2)[2]}`; a freeze is not cleared by hand")
            if self.control(key) is None:
                raise StoreError(f"no such anomaly {code}")
            self.delete_control(key)
            self.audit(now, "ANOMALY_CLEARED", code=key, operator=operator, reason=reason)

    # symbol dispositions for unmanaged broker holdings -------------------
    def set_disposition(self, now: datetime, symbol: str, disposition: str, operator: str) -> None:
        if disposition != "ignore":
            raise StoreError("the only disposition in v1 is 'ignore' (no adopt)")
        with self.tx():
            self.set_control(now, f"disposition:{symbol.upper()}", disposition)
            self.audit(now, "DISPOSITION", symbol=symbol.upper(), disposition=disposition,
                       operator=operator)

    def dispositions(self) -> dict[str, str]:
        return {k.split(":", 1)[1]: v for k, v in self.controls_with_prefix("disposition:").items()}

    # ---------------- risk days ----------------
    def risk_day(self, day: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM risk_days WHERE trading_day=?", (day,)).fetchone()

    def ensure_risk_day(self, day: str, sod_equity: float) -> sqlite3.Row:
        self.conn.execute(
            "INSERT OR IGNORE INTO risk_days(trading_day, sod_equity) VALUES (?, ?)", (day, sod_equity))
        return self.risk_day(day)

    def latch_daily_stop(self, now: datetime, day: str, reason: str) -> bool:
        with self.tx():
            cur = self.conn.execute(
                "UPDATE risk_days SET daily_stop_latched=1, latched_at=?, latched_reason=? "
                "WHERE trading_day=? AND daily_stop_latched=0", (_iso(now), reason, day))
            if cur.rowcount:
                self.audit(now, "DAILY_STOP_LATCHED", day=day, reason=reason)
            return bool(cur.rowcount)

    def realized_for_day(self, day: str) -> float:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(realized_pnl), 0) AS r FROM fills WHERE trading_day=?", (day,)).fetchone()
        return float(row["r"])

    def fees_for_day(self, day: str) -> float:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(c.commission), 0) AS fees FROM fills f "
            "JOIN commissions c ON c.exec_id = f.exec_id WHERE f.trading_day=?", (day,)).fetchone()
        return float(row["fees"])

    def fills_missing_commission(self, day: str) -> list[tuple[int, float]]:
        rows = self.conn.execute(
            "SELECT f.quantity, f.price FROM fills f LEFT JOIN commissions c ON c.exec_id=f.exec_id "
            "WHERE f.trading_day=? AND c.exec_id IS NULL", (day,)).fetchall()
        return [(int(r["quantity"]), float(r["price"])) for r in rows]

    # ---------------- intents ----------------
    def create_intent(self, now: datetime, req: OrderRequest, *, atr: float | None,
                      count_entry_day: str | None,
                      protection_plan: tuple[str, float, None] | None = None) -> None:
        """Persist the intent BEFORE any broker call. Entry submissions are
        counted in the same transaction (rejected/cancelled still count)."""
        if req.purpose == Purpose.PROTECT:
            if req.limit_price is not None:
                raise StoreError("new protective STP intent must have no limit")
        elif (req.limit_price is None or isinstance(req.limit_price, bool) or
              not isinstance(req.limit_price, (int, float)) or
              not math.isfinite(req.limit_price) or req.limit_price <= 0):
            raise StoreError("non-PROTECT intent needs finite positive limit")
        if protection_plan is not None and protection_plan[2] is not None:
            raise StoreError("new protective STP plan must have no limit")
        with self.tx():
            if count_entry_day:
                cur = self.conn.execute(
                    "UPDATE risk_days SET entries_submitted = entries_submitted + 1 WHERE trading_day=?",
                    (count_entry_day,))
                if cur.rowcount != 1:
                    raise StoreError("risk day missing; cannot count entry")
            self.conn.execute(
                "INSERT INTO intents(intent_id, symbol, side, quantity, limit_price, purpose, position_id, "
                "atr, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (req.intent_id, req.symbol, str(req.side), int(req.quantity), req.limit_price,
                 str(req.purpose), req.position_id, atr, str(IntentStatus.NEW), _iso(now), _iso(now)))
            self.audit(now, "INTENT_CREATED", intent_id=req.intent_id, symbol=req.symbol,
                       side=str(req.side), qty=req.quantity, limit=req.limit_price,
                       purpose=str(req.purpose))
            if protection_plan is not None:
                protect_id, trigger, limit = protection_plan
                self.conn.execute(
                    "INSERT INTO protections(position_id,intent_id,symbol,trigger_price,limit_price,order_type,state,updated_at) "
                    "VALUES (?,?,?,?,?,'STP','PLANNED',?)",
                    (req.position_id, protect_id, req.symbol, trigger, limit, _iso(now)))
                self.audit(now, "PROTECT_PLANNED", position_id=req.position_id,
                           intent_id=protect_id, trigger=trigger, limit=limit)

    def protection(self, position_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM protections WHERE position_id=?", (position_id,)).fetchone()

    def create_protection_plan(self, now: datetime, position_id: str, intent_id: str,
                               symbol: str, trigger: float, limit: float | None) -> None:
        if limit is not None or not math.isfinite(trigger) or trigger < 1:
            raise StoreError("new protection must be STP with valid stop and no limit")
        with self.tx():
            self.conn.execute(
                "INSERT INTO protections(position_id,intent_id,symbol,trigger_price,limit_price,order_type,state,updated_at) "
                "VALUES (?,?,?,?,?,'STP','PLANNED',?)",
                (position_id, intent_id, symbol, trigger, limit, _iso(now)))
            self.audit(now, "PROTECT_PLANNED", position_id=position_id,
                       intent_id=intent_id, trigger=trigger, limit=limit)

    def protection_for_intent(self, intent_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM protections WHERE intent_id=?", (intent_id,)).fetchone()

    def protections(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM protections ORDER BY position_id").fetchall()

    def open_protection_gaps_a(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT exec_id,position_id,started_at FROM protection_gap_a "
            "WHERE closed_at IS NULL ORDER BY started_at,exec_id").fetchall()

    def close_protection_gaps_a(self, now: datetime, position_id: str,
                                legacy_started_at: str | None) -> list[tuple[str | None, float]]:
        """Atomically close every booked BUY window after strict GTC proof.

        Old schema-4 files have no per-execution rows; retain their persisted
        position-level start as a clearly labelled legacy interval.
        """
        closed: list[tuple[str | None, float]] = []
        with self.tx():
            protection = self.protection(position_id)
            identity = ({"symbol": protection["symbol"], "intent_id": protection["intent_id"]}
                        if protection is not None else {})
            rows = self.conn.execute(
                "SELECT exec_id,started_at FROM protection_gap_a "
                "WHERE position_id=? AND closed_at IS NULL ORDER BY started_at,exec_id",
                (position_id,)).fetchall()
            for item in rows:
                seconds = max(0.0, (now - datetime.fromisoformat(item["started_at"])).total_seconds())
                recovered = self.control(f"recovered_gap_unverified:{item['exec_id']}") is not None
                self.audit(now, "PROTECT_GAP", gap_kind="A", secs=seconds,
                           position_id=position_id, exec_id=item["exec_id"],
                           recovered_unverified=recovered, **identity)
                self.conn.execute("UPDATE protection_gap_a SET closed_at=? WHERE exec_id=? AND closed_at IS NULL",
                                  (_iso(now), item["exec_id"]))
                closed.append((item["exec_id"], seconds))
            # A schema-4 file opened before this additive table existed may
            # already have an A window. Later partial fills create new rows;
            # their presence must not erase the older persisted start. A new
            # file has the first row and position start at exactly the same
            # instant, so it is not counted twice.
            legacy_is_older = (legacy_started_at is not None and
                               (not rows or legacy_started_at < rows[0]["started_at"]))
            if legacy_is_older:
                seconds = max(0.0, (now - datetime.fromisoformat(legacy_started_at)).total_seconds())
                self.audit(now, "PROTECT_GAP", gap_kind="A", secs=seconds,
                           position_id=position_id, legacy_window=True, **identity)
                closed.append((None, seconds))
            self.conn.execute(
                "UPDATE protections SET gap_a_started_at=NULL,updated_at=? WHERE position_id=?",
                (_iso(now), position_id))
        return closed

    def update_protection(self, now: datetime, position_id: str, **fields) -> None:
        allowed = {"intent_id", "account", "client_id", "con_id", "trigger_price", "limit_price", "requested_qty",
                   "confirmed_remaining", "pending_max_remaining", "pending_qty", "state",
                   "pending_trigger", "pending_limit", "modify_generation", "status_seen_generation",
                   "broker_order_id", "perm_id", "gap_a_started_at", "gap_b_started_at",
                   "confirmed_at",
                   "tp_stage", "tp_qty", "tp_intent_id"}
        if not set(fields) <= allowed:
            raise StoreError(f"bad protection fields {set(fields) - allowed}")
        fields = {k: _iso(v) if isinstance(v, datetime) else v for k, v in fields.items()}
        fields["updated_at"] = _iso(now)
        with self.tx():
            prior = self.protection(position_id)
            changed = prior is not None and any(prior[key] != value for key, value in fields.items()
                                               if key not in {"confirmed_at", "updated_at"})
            sets = ", ".join(f"{key}=?" for key in fields)
            result = self.conn.execute(f"UPDATE protections SET {sets} WHERE position_id=?",
                                       (*fields.values(), position_id))
            if result.rowcount != 1:
                raise StoreError(f"unknown protection position {position_id}")
            self.observation_audit(now, "PROTECT_STATE", changed=changed, position_id=position_id,
                       **{key: value for key, value in fields.items() if key != "updated_at"})

    def update_protection_intent_quantity(self, now: datetime, intent_id: str, quantity: int) -> None:
        if type(quantity) is not int or quantity < 1:
            raise StoreError("protection total quantity must be positive")
        with self.tx():
            row = self.intent(intent_id)
            if row is None or row["purpose"] != Purpose.PROTECT:
                raise StoreError("cannot change non-protection intent quantity")
            self.conn.execute("UPDATE intents SET quantity=?,updated_at=? WHERE intent_id=?",
                              (quantity, _iso(now), intent_id))

    def intent(self, intent_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM intents WHERE intent_id=?", (intent_id,)).fetchone()

    def active_intents(self) -> list[sqlite3.Row]:
        marks = ",".join("?" for _ in ACTIVE_STATUSES)
        return self.conn.execute(
            f"SELECT * FROM intents WHERE status IN ({marks}) ORDER BY created_at",
            tuple(str(s) for s in ACTIVE_STATUSES)).fetchall()

    def update_intent(self, now: datetime, intent_id: str, **fields) -> None:
        allowed = {"status", "broker_order_id", "perm_id", "last_raw_status", "sent_at",
                   "cancel_requested_at", "cancel_seen_at", "note", "hold_reason"}
        if not set(fields) <= allowed:
            raise StoreError(f"bad intent fields {set(fields) - allowed}")
        fields = {k: (_iso(v) if isinstance(v, datetime) else (str(v) if isinstance(v, IntentStatus) else v))
                  for k, v in fields.items()}
        fields["updated_at"] = _iso(now)
        sets = ", ".join(f"{k}=?" for k in fields)
        with self.tx():
            self.conn.execute(f"UPDATE intents SET {sets} WHERE intent_id=?", (*fields.values(), intent_id))
            if "status" in fields:
                self.audit(now, "INTENT_STATUS", intent_id=intent_id, status=fields["status"],
                           note=fields.get("note"))

    def hold_intent(self, now: datetime, intent_id: str, reason: str) -> bool:
        """UNKNOWN + hold: the engine will never finalize it from evidence
        that cannot cover it (e.g. an order from a previous session); only
        `resolve-intent` (operator) can. Returns True if newly held."""
        row = self.intent(intent_id)
        if row is None or row["hold_reason"]:
            return False
        self.update_intent(now, intent_id, status=IntentStatus.UNKNOWN, hold_reason=reason, note=reason)
        return True

    def intent_filled_qty(self, intent_id: str) -> int:
        row = self.conn.execute("SELECT COALESCE(SUM(quantity),0) AS q FROM fills WHERE intent_id=?",
                                (intent_id,)).fetchone()
        return int(row["q"])

    # ---------------- fills (exactly-once) ----------------
    def record_commission(self, now: datetime, exec_id: str, commission: float) -> bool:
        """Insert, or replace a different earlier value (IBKR commission
        correction; old and new are audited). Returns True if anything changed."""
        commission = float(commission)
        row = self.conn.execute("SELECT commission FROM commissions WHERE exec_id=?", (exec_id,)).fetchone()
        if row is None:
            self.conn.execute("INSERT INTO commissions(exec_id, commission, recorded_at) VALUES (?,?,?)",
                              (exec_id, commission, _iso(now)))
            return True
        if abs(float(row["commission"]) - commission) < 1e-9:
            return False
        self.conn.execute("UPDATE commissions SET commission=?, recorded_at=? WHERE exec_id=?",
                          (commission, _iso(now), exec_id))
        self.audit(now, "COMMISSION_CORRECTED", exec_id=exec_id, old=float(row["commission"]), new=commission)
        return True

    @staticmethod
    def _fill_time_identity(fill):
        return [fill.exec_id, fill.intent_id, fill.symbol, str(fill.side), int(fill.quantity),
                float(fill.price), fill.broker_order_id, fill.perm_id, fill.con_id]

    def archive_protection_identity(self, now, row):
        """Keep the exact retired GTC lineage before replacing the current row."""
        intent = self.intent(row["intent_id"])
        if (intent is None or intent["hold_reason"] or not row["broker_order_id"] or not row["perm_id"] or
                not row["con_id"] or not row["account"] or row["client_id"] is None or
                row["order_type"] != "STP" or not math.isfinite(row["trigger_price"]) or row["trigger_price"] < 1 or
                intent["broker_order_id"] != row["broker_order_id"] or intent["perm_id"] != row["perm_id"]):
            raise StoreError("retired protection identity incomplete")
        keys = ("position_id", "intent_id", "symbol", "broker_order_id", "perm_id", "con_id", "account", "client_id", "order_type", "trigger_price")
        identity = {key: row[key] for key in keys}
        identity["quantity"] = int(intent["quantity"])
        pending_stop = row["pending_trigger"]
        identity["stop_price_uncertain"] = pending_stop is not None and pending_stop != row["trigger_price"]
        identity["stop_price_candidates"] = ([row["trigger_price"], pending_stop]
                                              if identity["stop_price_uncertain"] else [])
        key = f"retired_protection:{row['intent_id']}"
        value = json.dumps(identity, sort_keys=True)
        with self.tx():
            prior = self.control(key)
            if prior is not None and prior != value:
                raise StoreError("retired protection lineage changed")
            self.set_control(now, key, value)
            self.audit(now, "PROTECT_IDENTITY_RETIRED", **identity)

    def retired_protection_matches(self, intent, fill):
        raw = self.control(f"retired_protection:{intent['intent_id']}")
        try:
            old = json.loads(raw) if raw else {}
        except (ValueError, TypeError):
            return False
        return bool(old.get("order_type") == "STP" and
                    isinstance(old.get("trigger_price"), (int, float)) and
                    math.isfinite(old["trigger_price"]) and old["trigger_price"] >= 1 and
                    old.get("intent_id") == intent["intent_id"] and
                    old.get("position_id") == intent["position_id"] and old.get("symbol") == fill.symbol and
                    old.get("account") and old.get("client_id") is not None and old.get("con_id") and
                    old.get("broker_order_id") == intent["broker_order_id"] == fill.broker_order_id and
                    old.get("perm_id") == intent["perm_id"] == fill.perm_id and old.get("con_id") == fill.con_id and
                    old.get("quantity") == intent["quantity"] and fill.side == Side.SELL and
                    not intent["hold_reason"])

    def _verify_execution_time(self, now, fill, known):
        # Only a COMPLETE query result may verify a timestamp; no callback
        # can undo it. Old rows have no marker and are never rewritten.
        if fill.time_source != "reqExecutions":
            return
        key = f"execution_time_unverified:{fill.exec_id}"
        if fill.time_uncertain:
            review = f"execution_time_review:{fill.exec_id}"
            if self.control(key) is not None and self.control(review) is None:
                self.set_control(now, review, json.dumps({"recorded": known["time"], "reported": None,
                                                         "new_unverified": True}))
                self.audit(now, "EXECUTION_TIME_REVIEW_REQUIRED", exec_id=fill.exec_id,
                           recorded=known["time"], reported=None, raw_time=fill.raw_time, source=fill.time_source,
                           reason="complete execution query timestamp has no explicit timezone; no economic rebooking")
            return
        raw = self.control(key)
        try:
            pending = json.loads(raw) if raw else None
        except (TypeError, ValueError):
            pending = None
        corrected = _iso(fill.time)
        identity = pending.get("identity") if isinstance(pending, dict) else None
        expected = self._fill_time_identity(fill)
        identity_matches = bool(isinstance(identity, list) and len(identity) == len(expected) and
            all(type(value) is type(bound) and value == bound for value, bound in zip(identity, expected)) and
            math.isfinite(expected[5]))
        if (isinstance(pending, dict) and identity_matches and
                pending.get("provisional_time") == known["time"] and
                trading_day(fill.time).isoformat() == known["trading_day"]):
            self.conn.execute("UPDATE fills SET time=? WHERE exec_id=?", (corrected, fill.exec_id))
            self.conn.execute("UPDATE positions SET closed_at=(SELECT MAX(time) FROM fills WHERE position_id=?) "
                              "WHERE position_id=? AND status='CLOSED'",
                              (known["position_id"], known["position_id"]))
            self.delete_control(key)
            self.audit(now, "EXECUTION_TIME_VERIFIED", exec_id=fill.exec_id,
                       previous=known["time"], verified=corrected, source=fill.time_source, raw_time=fill.raw_time)
            return
        if corrected == known["time"] and pending is None:
            return
        review = f"execution_time_review:{fill.exec_id}"
        if self.control(review) is None:
            self.set_control(now, review, json.dumps({"recorded": known["time"], "reported": corrected,
                                                     "new_unverified": pending is not None}))
            self.audit(now, "EXECUTION_TIME_REVIEW_REQUIRED", exec_id=fill.exec_id,
                       recorded=known["time"], reported=corrected, raw_time=fill.raw_time, source=fill.time_source,
                       reason="legacy record or nonmatching identity/trading day; no economic rebooking")

    def record_fill(self, now: datetime, fill: FillEvent, *, recovered: bool = False) -> FillOutcome:
        with self.tx():
            base = exec_base(fill.exec_id)
            known = self.conn.execute("SELECT * FROM fills WHERE exec_id=?", (fill.exec_id,)).fetchone()
            if known:
                self._verify_execution_time(now, fill, known)
                # replay (reqExecutions / reconnect): never re-apply quantity or
                # P&L, but DO backfill (or correct) the commission
                added = fill.commission is not None and self.record_commission(
                    now, fill.exec_id, fill.commission)
                return FillOutcome(inserted=False, commission_added=bool(added))
            if self.conn.execute("SELECT 1 FROM quarantined_fills WHERE exec_id=?", (fill.exec_id,)).fetchone():
                return FillOutcome(inserted=False)
            earlier = self.conn.execute(
                "SELECT exec_id, quantity, price FROM fills WHERE exec_base=? AND exec_id<>? UNION ALL "
                "SELECT exec_id, json_extract(payload,'$.qty'), json_extract(payload,'$.price') "
                "FROM quarantined_fills WHERE exec_base=? AND exec_id<>? LIMIT 1",
                (base, fill.exec_id, base, fill.exec_id)).fetchone()
            if earlier is not None:
                # IBKR execution correction: book NOTHING automatically. The
                # position keeps the original values and entries stay blocked
                # until the operator has checked it in TWS.
                payload = {"symbol": fill.symbol, "side": str(fill.side), "qty": fill.quantity,
                           "price": fill.price, "intent_id": fill.intent_id, "time": _iso(fill.time),
                           "corrects": earlier["exec_id"], "original_qty": earlier["quantity"],
                           "original_price": earlier["price"]}
                self.conn.execute("INSERT INTO quarantined_fills(exec_id, exec_base, payload, recorded_at) "
                                  "VALUES (?,?,?,?)", (fill.exec_id, base, json.dumps(payload), _iso(now)))
                code = f"exec_correction:{fill.exec_id}"
                self.raise_anomaly(now, code, f"{fill.symbol} execution {earlier['exec_id']} corrected to "
                                              f"{fill.quantity}@{fill.price} (was {earlier['quantity']}@"
                                              f"{earlier['price']}); not booked -- verify in TWS; if the position is wrong, flatten and `release`")
                return FillOutcome(inserted=False, anomaly=code)
            intent = self.intent(fill.intent_id) if fill.intent_id else None
            # Business end point: once the symbol was verified flat (release /
            # freeze resolution: broker 0 shares and NO working order), every
            # execution of an order created before that point already happened
            # and was part of what the operator flattened. Reported now, it is
            # evidence only -- quarantined BEFORE any booking: no position is
            # created, grown or closed, and it never enters lots or day P&L.
            # (Local times only: the order's creation vs the verification.)
            flat_at = self.flat_point(intent["symbol"]) if intent is not None else None
            if flat_at is not None and datetime.fromisoformat(intent["created_at"]) <= flat_at:
                self._quarantine(now, fill, f"order created before {intent['symbol']} was verified flat at "
                                            f"{_iso(flat_at)}")
                code = f"late_fill_before_flat:{fill.exec_id}"
                self.raise_anomaly(now, code, f"{fill.symbol} {fill.side} {fill.quantity}@{fill.price} of "
                                              f"{intent['intent_id']} (created {intent['created_at']}) reported after "
                                              f"the symbol was verified flat at {_iso(flat_at)}; not booked -- check "
                                              "TWS for these shares")
                return FillOutcome(inserted=False, anomaly=code)
            # an order of a RELEASED position (or of its released recovery
            # position): same end point, evidence only, never booked
            released = None
            if intent is not None:
                for pid in (intent["position_id"], intent["recovery_position_id"]):
                    row = self.conn.execute("SELECT status FROM positions WHERE position_id=?", (pid,)).fetchone() \
                        if pid else None
                    if row is not None and row["status"] == "RELEASED":
                        released = pid
            if released is not None:
                self._quarantine(now, fill, f"order of released position {released}")
                code = f"late_fill_after_release:{fill.exec_id}"
                self.raise_anomaly(now, code, f"{fill.symbol} {fill.side} {fill.quantity}@{fill.price} of "
                                              f"{intent['intent_id']} reported after position {released} was "
                                              "released; not booked -- check TWS for these shares")
                return FillOutcome(inserted=False, anomaly=code)
            if intent is not None and intent["purpose"] == Purpose.PROTECT:
                current = self.protection(intent["position_id"])
                if current is not None and current["intent_id"] == intent["intent_id"] and current["order_type"] != "STP":
                    self._quarantine(now, fill, "legacy protection type needs manual treatment")
                    code = f"legacy_protection:{fill.exec_id}"
                    self.raise_anomaly(now, code, "legacy STP LMT protection; not automatically converted or booked")
                    return FillOutcome(inserted=False, anomaly=code)
                if current is None or current["intent_id"] != intent["intent_id"]:
                    if not self.retired_protection_matches(intent, fill):
                        self._quarantine(now, fill, "retired GTC identity missing or mismatched")
                        code = f"retired_protection_identity:{fill.exec_id}"
                        self.raise_anomaly(now, code, "late GTC execution lacks exact immutable lineage; no booking")
                        return FillOutcome(inserted=False, anomaly=code)
            position_id = intent["position_id"] if intent else None
            self.conn.execute(
                "INSERT INTO fills(exec_id, intent_id, position_id, symbol, side, quantity, price, "
                "time, trading_day, broker_order_id, perm_id, exec_base) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (fill.exec_id, fill.intent_id or None, position_id, fill.symbol.upper(), str(fill.side),
                 int(fill.quantity), float(fill.price), _iso(fill.time),
                 trading_day(fill.time).isoformat(), fill.broker_order_id, fill.perm_id, base))
            if fill.time_uncertain:
                self.set_control(now, f"execution_time_unverified:{fill.exec_id}",
                                 json.dumps({"provisional_time": _iso(fill.time),
                                             "identity": self._fill_time_identity(fill)}))
                self.audit(now, "EXECUTION_TIME_UNVERIFIED", exec_id=fill.exec_id,
                           provisional=_iso(fill.time), source=fill.time_source, raw_time=fill.raw_time)

            if fill.commission is not None:
                self.record_commission(now, fill.exec_id, fill.commission)
            self.audit(now, "FILL", exec_id=fill.exec_id, intent_id=fill.intent_id, symbol=fill.symbol,
                       side=str(fill.side), qty=fill.quantity, price=fill.price)

            if intent is None:
                code = f"foreign_fill:{fill.exec_id}"
                self.raise_anomaly(now, code, f"{fill.symbol} {fill.side} {fill.quantity}@{fill.price} "
                                              f"has no V6-Lite intent")
                return FillOutcome(inserted=True, anomaly=code)
            if intent["symbol"] != fill.symbol.upper() or intent["side"] != str(fill.side):
                code = f"fill_mismatch:{fill.exec_id}"
                self.raise_anomaly(now, code, "fill symbol/side differs from intent")
                return FillOutcome(inserted=True, anomaly=code)

            purpose = Purpose(intent["purpose"])
            if purpose is Purpose.ENTRY:
                outcome = self._apply_entry_fill(now, intent, fill)
                target = self.conn.execute("SELECT position_id FROM fills WHERE exec_id=?",
                                           (fill.exec_id,)).fetchone()
                position = self.position(target["position_id"]) if target and target["position_id"] else None
                if outcome.inserted and position is not None and position["status"] == "OPEN" and \
                        self.protection(position["position_id"]) is not None:
                    # On recovery, the local callback starts late. The broker
                    # execution time gives a conservative lower bound, but a
                    # missed process interval is never called verified.
                    started_at = min(now, fill.time) if recovered else now
                    self.conn.execute(
                        "INSERT OR IGNORE INTO protection_gap_a(exec_id,position_id,started_at) VALUES (?,?,?)",
                        (fill.exec_id, position["position_id"], _iso(started_at)))
                    if recovered:
                        self.set_control(now, f"recovered_gap_unverified:{fill.exec_id}",
                                         f"broker_time={_iso(fill.time)} received_at={_iso(now)}")
                return outcome
            outcome = self._apply_exit_fill(now, intent, fill, purpose)
            if purpose in {Purpose.TP1, Purpose.TP2, Purpose.TP3} and outcome.inserted:
                self._audit_tp_fill(now, intent, fill)
            return outcome

    def _audit_tp_fill(self, now, intent, fill):
        """Optional evidence is decoded defensively inside economic booking."""
        raw = self.control(f"tp_send:{intent['intent_id']}")
        diagnostics = []
        try:
            parsed = json.loads(raw) if raw else {}
        except (ValueError, TypeError):
            parsed = {}
            diagnostics.append("invalid_send_json")
        if not isinstance(parsed, dict):
            parsed = {}
            diagnostics.append("send_not_object")
        def safe_json(value):
            if isinstance(value, float) and not math.isfinite(value):
                diagnostics.append("nonfinite_send_field")
                return None  # raw JSON is retained separately; canonical numeric is unknown
            if isinstance(value, dict):
                return {key: safe_json(item) for key, item in value.items()}
            if isinstance(value, list):
                return [safe_json(item) for item in value]
            return value
        sent = safe_json(parsed)
        for field in ("bid", "ask", "calculated_price", "sent_price", "working_price", "aux_price",
                      "mkt_cap_price", "quote_age_seconds", "P", "S", "T", "X"):
            value = sent.get(field)
            try:
                valid = value is None or (isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value))
            except (ValueError, TypeError, OverflowError):
                valid = False
            if not valid:
                sent[field] = None
                diagnostics.append("invalid_send_field:" + field)
        row = self.protection(intent["position_id"])
        bid_raw = parsed.get("bid")
        try:
            valid_bid = (isinstance(bid_raw, (int, float)) and not isinstance(bid_raw, bool) and
                         math.isfinite(bid_raw) and 0 < bid_raw < 1e308)
        except (ValueError, TypeError, OverflowError):
            valid_bid = False
        bid = bid_raw if valid_bid else None
        if raw and not valid_bid:
            diagnostics.append("send_bid_unknown")
        difference = fill.price - bid if bid is not None and not fill.time_uncertain else None
        secs = None
        gap_raw = row["gap_b_started_at"] if row is not None else None
        if gap_raw:
            try:
                started = datetime.fromisoformat(gap_raw)
                if started.tzinfo is None or now.tzinfo is None or started > now:
                    raise ValueError("invalid gap clock")
                secs = (now - started).total_seconds()
            except (ValueError, TypeError, OverflowError):
                diagnostics.append("gap_clock_unknown")
        payload = dict(sent)
        payload["bid"] = bid
        if not valid_bid:
            payload["bid_raw"] = str(bid_raw)
        if diagnostics:
            payload.update(evidence_diagnostics=diagnostics, send_evidence_raw=raw, gap_b_started_at_raw=gap_raw)
        pos = self.position(intent["position_id"])
        current_t = sum(max(0, int(i["quantity"]) - self.intent_filled_qty(i["intent_id"]))
                        for i in self.active_intents_for_position(intent["position_id"])
                        if i["side"] == "SELL" and i["purpose"] != Purpose.PROTECT)
        payload.update(P=pos["qty_open"] if pos and pos["status"] == "OPEN" else 0,
                       S=max(row["confirmed_remaining"], row["pending_max_remaining"]) if row else None,
                       T=current_t, X=None, X_at_send=sent.get("X"), three_source_complete=False)
        def finite_result(value):
            return value if value is not None and math.isfinite(value) else None
        difference = finite_result(difference)
        difference_usd = finite_result(fill.quantity * difference) if difference is not None else None
        difference_bps = finite_result(10000 * difference / bid) if difference is not None and bid else None
        payload.update(phase="fill", source=fill.time_source, position_id=intent["position_id"],
            symbol=intent["symbol"], stage=intent["purpose"], intent_id=intent["intent_id"],
            exec_id=fill.exec_id, quantity=fill.quantity, fill_price=fill.price, bid_at_send=bid,
            fill_minus_bid=difference, fill_minus_bid_usd=difference_usd,
            fill_minus_bid_bps=difference_bps,
            gap_b_secs=secs, fill_time=_iso(fill.time), receive_time=_iso(now), time_uncertain=fill.time_uncertain,
            trading_day=trading_day(fill.time).isoformat())
        self.audit(now, "TP_EVIDENCE", **payload)

    def _insert_position(self, now, pid: str, symbol: str, source: str, price: float, atr: float,
                         qty: int) -> None:
        levels = self._levels_fn(float(price), float(atr))
        self.conn.execute(
            "INSERT INTO positions(position_id, symbol, status, source, entry_price, atr, qty_initial, "
            "qty_open, stop, tp1, tp2, tp3, opened_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (pid, symbol.upper(), "OPEN", source, float(price), float(atr), qty, qty,
             levels.stop, levels.tp1, levels.tp2, levels.tp3, _iso(now)))

    def _add_shares(self, pos, qty: int, price: float) -> None:
        """Add bought shares to an OPEN position. Cost basis = average over the
        shares still open; the exit plan is frozen once a stage exists."""
        pid = pos["position_id"]
        stages = self.conn.execute("SELECT COUNT(*) AS n FROM exit_stages WHERE position_id=?",
                                   (pid,)).fetchone()["n"]
        open_after = pos["qty_open"] + qty
        avg = (pos["entry_price"] * pos["qty_open"] + price * qty) / open_after
        if stages:
            self.conn.execute(
                "UPDATE positions SET qty_initial=qty_initial+?, qty_open=?, entry_price=? WHERE position_id=?",
                (qty, open_after, avg, pid))
        else:
            levels = self._levels_fn(avg, float(pos["atr"]))
            self.conn.execute(
                "UPDATE positions SET qty_initial=qty_initial+?, qty_open=?, entry_price=?, stop=?, tp1=?, "
                "tp2=?, tp3=? WHERE position_id=?",
                (qty, open_after, avg, levels.stop, levels.tp1, levels.tp2, levels.tp3, pid))

    def _open_position_for(self, symbol: str):
        return self.conn.execute("SELECT * FROM positions WHERE symbol=? AND status='OPEN'",
                                 (symbol.upper(),)).fetchone()

    def _apply_entry_fill(self, now, intent, fill: FillEvent) -> FillOutcome:
        pid = intent["position_id"]
        pos = self.conn.execute("SELECT * FROM positions WHERE position_id=?", (pid,)).fetchone()
        if pos is None:
            other = self._open_position_for(fill.symbol)
            if other is None:
                self._insert_position(now, pid, fill.symbol, "strategy", fill.price, intent["atr"], fill.quantity)
                return FillOutcome(inserted=True)
            # the shares are real: count them in the open position (never drop
            # quantity), but stop new risk until the operator looks at it
            return self._route_extra_entry(now, intent, fill, other, "duplicate_open_position",
                                           f"entry {intent['intent_id']} filled while {other['position_id']} "
                                           f"was already open")
        if pos["status"] == "OPEN":
            self._add_shares(pos, fill.quantity, float(fill.price))
            return FillOutcome(inserted=True)
        if pos["status"] == "RELEASED":
            # the operator flattened and released this position: it is a
            # finished business record. An older BUY reported now is kept as
            # evidence (on the released row, outside day P&L) but never
            # re-creates or grows a position; whatever the broker really
            # holds shows up in the next fresh reconcile as unmanaged.
            code = f"late_fill_after_release:{fill.exec_id}"
            self.raise_anomaly(now, code, f"{fill.symbol} {fill.quantity} bought by {intent['intent_id']} reported "
                                          f"after position {pid} was released; check TWS for these shares")
            return FillOutcome(inserted=True, anomaly=code)
        # Late ENTRY fill after the position was fully exited (fills of orders
        # created before a verified-flat point never get here: record_fill
        # quarantines them first).
        # Every batch of this intent goes to ONE recovery position (persisted
        # on the intent), merged instead of colliding.
        target = None
        if intent["recovery_position_id"]:
            target = self.conn.execute("SELECT * FROM positions WHERE position_id=? AND status='OPEN'",
                                       (intent["recovery_position_id"],)).fetchone()
        target = target or self._open_position_for(fill.symbol)
        return self._route_extra_entry(now, intent, fill, target, "late_entry_fill",
                                       f"{fill.symbol} bought after position {pid} closed")

    def _route_extra_entry(self, now, intent, fill: FillEvent, target, kind: str, why: str) -> FillOutcome:
        if target is None:
            new_pid = new_id("pos")
            self._insert_position(now, new_pid, fill.symbol, "late_fill", fill.price, intent["atr"], fill.quantity)
            target_pid = new_pid
        else:
            self._add_shares(target, fill.quantity, float(fill.price))
            target_pid = target["position_id"]
        self.conn.execute("UPDATE fills SET position_id=? WHERE exec_id=?", (target_pid, fill.exec_id))
        self.conn.execute("UPDATE intents SET recovery_position_id=? WHERE intent_id=?",
                          (target_pid, intent["intent_id"]))
        code = f"{kind}:{intent['intent_id']}"
        self.raise_anomaly(now, code, f"{why}; {fill.quantity} shares now managed in {target_pid}")
        return FillOutcome(inserted=True, anomaly=code)

    def _audit_protection_fill(self, now, intent, fill, *, held_after, confirmed_remaining, pending_max_remaining):
        protection = self.protection(intent["position_id"])
        retired = protection is None or protection["intent_id"] != intent["intent_id"]
        candidates = []
        if retired:
            raw = self.control(f"retired_protection:{intent['intent_id']}")
            identity = json.loads(raw) if raw else {}
            stop = identity.get("trigger_price") if identity.get("order_type") == "STP" else None
            if identity.get("stop_price_uncertain"):
                candidates = identity.get("stop_price_candidates", [])
                stop = None
        else:
            stop = protection["trigger_price"] if protection["order_type"] == "STP" else None
            if (protection["pending_trigger"] is not None and protection["pending_trigger"] != stop):
                candidates = [stop, protection["pending_trigger"]]
                stop = None  # execution callback cannot prove which auxPrice was active
        if not isinstance(stop, (int, float)) or not math.isfinite(stop) or stop <= 0:
            stop = None
        avg = self.conn.execute("SELECT SUM(quantity*price)/SUM(quantity) FROM fills WHERE intent_id=?", (intent["intent_id"],)).fetchone()[0]
        usd = fill.quantity * (stop - fill.price) if stop is not None else None
        bps = (stop - fill.price) / stop * 10000 if stop is not None else None
        payload = dict(position_id=intent["position_id"], intent_id=intent["intent_id"], exec_id=fill.exec_id,
                       symbol=fill.symbol, quantity=fill.quantity, qty=fill.quantity,
                       stop_price=stop, fill_price=fill.price, avg_fill_price=avg,
                       slippage_usd=usd, slippage_bps=bps, bps=bps, stop_price_candidates=candidates,
                       slippage_status="known" if stop is not None else "unknown", retired=retired,
                       time=_iso(fill.time), trading_day=trading_day(fill.time).isoformat(),
                       held_after=held_after, confirmed_remaining=confirmed_remaining,
                       pending_max_remaining=pending_max_remaining)
        self.audit(now, "PROTECT_FILL", **payload)
        if retired:
            self.audit(now, "PROTECT_RETIRED_FILL", **payload)

    def _apply_exit_fill(self, now, intent, fill: FillEvent, stage: Purpose) -> FillOutcome:
        pid = intent["position_id"]
        pos = self.conn.execute("SELECT * FROM positions WHERE position_id=?", (pid,)).fetchone()
        if pos is None:
            code = f"exit_without_position:{fill.exec_id}"
            self.raise_anomaly(now, code, f"exit fill for unknown position {pid}")
            return FillOutcome(inserted=True, anomaly=code)
        if pos["status"] != "OPEN":
            if stage is Purpose.PROTECT:
                protection = self.protection(pid)
                self._audit_protection_fill(now, intent, fill, held_after=pos["qty_open"],
                    confirmed_remaining=protection["confirmed_remaining"] if protection else 0,
                    pending_max_remaining=protection["pending_max_remaining"] if protection else 0)
            # e.g. an old SELL reported after the position was released: the
            # broker already reflects it; never re-open or re-close the row
            code = f"exit_after_close:{fill.exec_id}"
            self.raise_anomaly(now, code, f"{fill.symbol} {fill.quantity} sold by {intent['intent_id']} but "
                                          f"position {pid} is {pos['status']}; check TWS for a short position")
            return FillOutcome(inserted=True, anomaly=code)
        applied = min(fill.quantity, pos["qty_open"])
        anomaly = None
        if fill.quantity > pos["qty_open"]:
            anomaly = f"oversell:{fill.exec_id}"
            self.raise_anomaly(now, anomaly, f"{fill.symbol} sold {fill.quantity} with only "
                                             f"{pos['qty_open']} open")
        realized = (fill.price - pos["entry_price"]) * applied
        remaining = pos["qty_open"] - applied
        closed = remaining == 0
        # closed_at = the LATEST execution time among the position's fills
        # (not the booking time, not the last-booked fill): the loss streak
        # is ordered by it. This fill is already inserted.
        closed_at = None
        if closed:
            closed_at = self.conn.execute("SELECT MAX(time) AS t FROM fills WHERE position_id=?",
                                          (pid,)).fetchone()["t"] or _iso(fill.time)
        self.conn.execute(
            "UPDATE positions SET qty_open=?, realized_pnl=realized_pnl+?, status=?, closed_at=? "
            "WHERE position_id=?",
            (remaining, realized, "CLOSED" if closed else "OPEN", closed_at, pid))
        if stage is Purpose.PROTECT:
            # record_fill owns the transaction: broker protection executions
            # reduce the position and every possible GTC remainder atomically.
            # pending_qty is the broker's *totalQuantity*, not a remainder;
            # confirmation subtracts cumulative executions from it later.
            protection = self.protection(pid)
            if protection is None:
                raise StoreError("protection fill has no durable GTC identity")
            retired = protection["intent_id"] != intent["intent_id"]
            confirmed_left = (int(protection["confirmed_remaining"]) if retired else
                              max(0, int(protection["confirmed_remaining"]) - fill.quantity))
            possible_left = (int(protection["pending_max_remaining"]) if retired else
                             max(0, int(protection["pending_max_remaining"]) - fill.quantity))
            self.conn.execute(
                "UPDATE protections SET confirmed_remaining=?, pending_max_remaining=?, "
                "state=?, updated_at=? WHERE position_id=?",
                (confirmed_left, possible_left,
                 "FILLED" if max(int(protection["confirmed_remaining"]),
                                 int(protection["pending_max_remaining"])) > 0 and
                                 confirmed_left == 0 and possible_left == 0 else protection["state"],
                 _iso(now), pid),
            )
            self._audit_protection_fill(now, intent, fill, held_after=remaining,
                                        confirmed_remaining=confirmed_left, pending_max_remaining=possible_left)
        self.conn.execute(
            "UPDATE exit_stages SET filled_qty=filled_qty+?, updated_at=? WHERE position_id=? AND stage=?",
            (fill.quantity, _iso(now), pid, str(stage)))
        self.conn.execute("UPDATE fills SET realized_pnl=? WHERE exec_id=?", (realized, fill.exec_id))
        return FillOutcome(inserted=True, closed_position_id=pid if closed else None, anomaly=anomaly)

    # ---------------- positions ----------------
    def open_positions(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM positions WHERE status='OPEN' ORDER BY opened_at").fetchall()

    def position(self, position_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM positions WHERE position_id=?", (position_id,)).fetchone()

    @staticmethod
    def view(row: sqlite3.Row) -> PositionView:
        return PositionView(
            position_id=row["position_id"], symbol=row["symbol"], entry_price=row["entry_price"],
            qty_initial=row["qty_initial"], qty_open=row["qty_open"], stop=row["stop"], tp1=row["tp1"],
            tp2=row["tp2"], tp3=row["tp3"], tp1_done=bool(row["tp1_done"]), tp2_done=bool(row["tp2_done"]),
            breakeven_stop=row["breakeven_stop"])

    def position_fees_complete(self, position_id: str) -> bool:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM fills f LEFT JOIN commissions c ON c.exec_id=f.exec_id "
            "WHERE f.position_id=? AND c.exec_id IS NULL", (position_id,)).fetchone()
        return row["n"] == 0

    def _quarantine(self, now: datetime, fill: FillEvent, reason: str) -> bool:
        """Keep an execution out of the ledger (audited; caller holds the tx)."""
        payload = {"symbol": fill.symbol, "side": str(fill.side), "qty": fill.quantity, "price": fill.price,
                   "intent_id": fill.intent_id, "time": _iso(fill.time), "reason": reason}
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO quarantined_fills(exec_id, exec_base, payload, recorded_at) VALUES (?,?,?,?)",
            (fill.exec_id, exec_base(fill.exec_id), json.dumps(payload), _iso(now)))
        if cur.rowcount:
            self.audit(now, "FILL_QUARANTINED", exec_id=fill.exec_id, **payload)
        return bool(cur.rowcount)

    def _commit_completed_cancel_proofs(self, now, symbol, proofs):
        # Called INSIDE the existing final release transaction. Failed final
        # guards roll back both proof audit and release; never synthesize a
        # CANCEL_SEEN state before flat/quiet/execution checks have all passed.
        from v6lite.release_evidence import completed_cancel_proof, proof_audit
        for proof in proofs:
            old = dict(proof.protection)
            current = self.protection(old['position_id'])
            intent = self.intent(old['intent_id'])
            position = self.position(old['position_id'])
            if (old['symbol'] != symbol or current is None or intent is None or position is None or
                    dict(current) != old or dict(intent) != dict(proof.intent) or
                    dict(position) != dict(proof.position) or
                    self.intent_filled_qty(old['intent_id']) != proof.booked_filled):
                raise StoreError('completed proof local binding changed before release')
            completed_cancel_proof(proof.snapshot, current, intent, position, proof.booked_filled, now)
            self.audit(now, 'PROTECT_CANCEL_PROVEN_COMPLETED', **proof_audit(proof))

    def release_position(self, now: datetime, symbol: str, *, day: str, equity: float, operator: str,
                         reason: str, completed_cancel_proofs=()) -> str:
        """Close the managed position of `symbol` after the operator flattened
        it (the caller proved: fresh broker position 0, nothing working). In
        ONE transaction: position RELEASED (qty 0, not a trade), active held
        intents of the symbol closed, the release recorded, and the day's
        entry freeze latched -- a crash can never keep one without the other."""
        sym = symbol.upper()
        rid = new_id("release")
        with self.tx():
            self._commit_completed_cancel_proofs(now, sym, completed_cancel_proofs)
            pos = self._open_position_for(sym)
            if pos is None:
                raise StoreError(f"{sym} has no managed open position")
            self.conn.execute("UPDATE positions SET status='RELEASED', qty_open=0, closed_at=? WHERE position_id=?",
                              (_iso(now), pos["position_id"]))
            marks = ",".join("?" for _ in ACTIVE_STATUSES)
            self.conn.execute(
                f"UPDATE intents SET status=?, note=?, updated_at=? WHERE symbol=? AND status IN ({marks})",
                (str(IntentStatus.CANCELLED), f"closed_by_release:{rid}", _iso(now), sym,
                 *(str(x) for x in ACTIVE_STATUSES)))
            self.conn.execute(
                "INSERT INTO releases(release_id, symbol, at, position_id, ledger_qty, operator, reason) "
                "VALUES (?,?,?,?,?,?,?)", (rid, sym, _iso(now), pos["position_id"], pos["qty_open"], operator, reason))
            # the freeze ends with the position it was about
            self.set_control(now, f"flat_verified:{sym}", _iso(now))
            self.delete_control(f"frozen:{sym}")
            self.delete_control(f"anomaly:frozen:{sym}")
            self.ensure_risk_day(day, equity)
            cur = self.conn.execute(
                "UPDATE risk_days SET daily_stop_latched=1, latched_at=?, latched_reason=? "
                "WHERE trading_day=? AND daily_stop_latched=0",
                (_iso(now), f"release {sym} ({rid}): the day's P&L is not provable", day))
            self.audit(now, "POSITION_RELEASED", release_id=rid, symbol=sym, position_id=pos["position_id"],
                       ledger_qty=pos["qty_open"], operator=operator, reason=reason, day_latched=bool(cur.rowcount))
        return rid

    def legacy_evidence_fills(self) -> list[str]:
        """Evidence-only fills that EARLIER versions inserted into `fills`
        (where fees and lots would count them): batch 5 for
        `late_fill_before_flat`, batches 4-5 for `late_fill_after_release`,
        and `exit_after_close` on a released position. This version
        quarantines all of them before booking; old rows cannot be told
        apart from real trades by the P&L code, so the state is refused."""
        codes = self.conn.execute(
            "SELECT json_extract(payload, '$.code') AS code FROM audit WHERE kind='ANOMALY' AND ("
            "payload LIKE '%late_fill_before_flat:%' OR payload LIKE '%late_fill_after_release:%' "
            "OR payload LIKE '%exit_after_close:%')").fetchall()
        found = set()
        for r in codes:
            if not r["code"] or ":" not in r["code"]:
                continue
            kind, exec_id = r["code"].split(":", 1)
            row = self.conn.execute(
                "SELECT p.status FROM fills f LEFT JOIN positions p ON p.position_id=f.position_id "
                "WHERE f.exec_id=?", (exec_id,)).fetchone()
            if row is None:
                continue                                   # quarantined, not booked: fine
            if kind == "exit_after_close" and row["status"] != "RELEASED":
                continue                                   # a real sale after a normal close
            found.add(exec_id)
        return sorted(found)

    def flat_point(self, symbol: str) -> datetime | None:
        """Latest time the symbol was verified flat: a release (also those
        recorded by earlier schema-4 versions, which wrote no control) or a
        freeze resolution."""
        sym = symbol.upper()
        points = []
        control = self.control(f"flat_verified:{sym}")
        if control:
            points.append(datetime.fromisoformat(control))
        row = self.conn.execute("SELECT MAX(at) AS t FROM releases WHERE symbol=?", (sym,)).fetchone()
        if row["t"]:
            points.append(datetime.fromisoformat(row["t"]))
        return max(points) if points else None

    def resolve_freeze(self, now: datetime, symbol: str, *, day: str, equity: float, operator: str,
                       reason: str, completed_cancel_proofs=()) -> None:
        """End the freeze of a symbol whose position was CLOSED by booked fills
        while frozen (the caller proved the same flat, quiet, nothing-working
        state as for a release). The closed trade stands as booked -- it is
        already a loss-streak event of its own, so no release record (no second
        loss). Remaining active intents of the symbol are closed and the day's
        entry freeze is latched (an unexplained difference happened today)."""
        sym = symbol.upper()
        with self.tx():
            self._commit_completed_cancel_proofs(now, sym, completed_cancel_proofs)
            if self._open_position_for(sym) is not None:
                raise StoreError(f"{sym} still has an open position: use release")
            if self.control(f"frozen:{sym}") is None:
                raise StoreError(f"{sym} is not frozen")
            marks = ",".join("?" for _ in ACTIVE_STATUSES)
            self.conn.execute(
                f"UPDATE intents SET status=?, note=?, updated_at=? WHERE symbol=? AND status IN ({marks})",
                (str(IntentStatus.CANCELLED), "closed_by_freeze_resolution", _iso(now), sym,
                 *(str(x) for x in ACTIVE_STATUSES)))
            self.set_control(now, f"flat_verified:{sym}", _iso(now))
            self.delete_control(f"frozen:{sym}")
            self.delete_control(f"anomaly:frozen:{sym}")
            self.ensure_risk_day(day, equity)
            cur = self.conn.execute(
                "UPDATE risk_days SET daily_stop_latched=1, latched_at=?, latched_reason=? "
                "WHERE trading_day=? AND daily_stop_latched=0",
                (_iso(now), f"freeze of {sym} resolved: unexplained quantity difference today", day))
            self.audit(now, "FREEZE_RESOLVED", symbol=sym, operator=operator, reason=reason,
                       day_latched=bool(cur.rowcount))

    def closed_trades(self) -> list[dict]:
        """ALL closed positions in execution-time order of their closing fill,
        with known fees and the fills whose commission has not arrived yet.
        (Released positions have no provable P&L and are not trades.)"""
        rows = self.conn.execute(
            "SELECT position_id, symbol, closed_at, realized_pnl FROM positions "
            "WHERE status='CLOSED' AND closed_at IS NOT NULL ORDER BY closed_at, opened_at, position_id").fetchall()
        fills: dict[str, list] = {}
        for f in self.conn.execute(
                "SELECT f.position_id, f.quantity, f.price, c.commission FROM fills f "
                "JOIN positions p ON p.position_id=f.position_id AND p.status='CLOSED' "
                "LEFT JOIN commissions c ON c.exec_id=f.exec_id").fetchall():
            fills.setdefault(f["position_id"], []).append(f)
        out = []
        for row in rows:
            mine = fills.get(row["position_id"], [])
            out.append({
                "position_id": row["position_id"], "symbol": row["symbol"],
                "closed_at": datetime.fromisoformat(row["closed_at"]), "gross": float(row["realized_pnl"]),
                "fees": sum(float(f["commission"]) for f in mine if f["commission"] is not None),
                "missing": [(int(f["quantity"]), float(f["price"])) for f in mine if f["commission"] is None],
            })
        return out

    def release_times(self) -> list[datetime]:
        return [datetime.fromisoformat(r["at"]) for r in self.conn.execute(
            "SELECT at FROM releases ORDER BY at").fetchall()]

    def entry_fills_since(self, since: datetime) -> list[sqlite3.Row]:
        """V6-Lite BUY executions after `since` (not yet reflected in a funds
        value that last changed at `since`)."""
        return self.conn.execute(
            "SELECT f.* FROM fills f JOIN intents i ON i.intent_id=f.intent_id "
            "WHERE f.side='BUY' AND i.purpose='ENTRY' AND f.time>?", (_iso(since),)).fetchall()

    def positions_for_day(self, day: str) -> list[sqlite3.Row]:
        """Positions that matter for the day's P&L: open now, or closed after
        trading today. RELEASED positions are excluded (their P&L is not
        provable; a release freezes entries for the day instead)."""
        return self.conn.execute(
            "SELECT * FROM positions WHERE status='OPEN' OR (status='CLOSED' AND position_id IN "
            "(SELECT position_id FROM fills WHERE trading_day=? AND position_id IS NOT NULL)) "
            "ORDER BY opened_at", (day,)).fetchall()

    def position_fills(self, position_id: str) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM fills WHERE position_id=? ORDER BY time, exec_id",
                                 (position_id,)).fetchall()

    def position_net_pnl(self, position_id: str) -> float:
        pos = self.position(position_id)
        fees = self.conn.execute(
            "SELECT COALESCE(SUM(c.commission),0) AS f FROM fills f JOIN commissions c ON c.exec_id=f.exec_id "
            "WHERE f.position_id=?", (position_id,)).fetchone()["f"]
        return float(pos["realized_pnl"]) - float(fees)

    # ---------------- exit stages (one-shot) ----------------
    def begin_stage(self, now: datetime, position_id: str, stage: Purpose, target_qty: int) -> bool:
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO exit_stages(position_id, stage, status, target_qty, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?)", (position_id, str(stage), "PENDING", target_qty, _iso(now), _iso(now)))
        if cur.rowcount:
            self.audit(now, "STAGE_BEGIN", position_id=position_id, stage=str(stage), target=target_qty)
        return bool(cur.rowcount)

    def stage(self, position_id: str, stage: Purpose) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM exit_stages WHERE position_id=? AND stage=?",
                                 (position_id, str(stage))).fetchone()

    def pending_stages(self, position_id: str) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM exit_stages WHERE position_id=? AND status='PENDING'",
                                 (position_id,)).fetchall()

    def bump_stage_attempt(self, now: datetime, position_id: str, stage: Purpose) -> None:
        self.conn.execute("UPDATE exit_stages SET attempts=attempts+1, updated_at=? WHERE position_id=? AND stage=?",
                          (_iso(now), position_id, str(stage)))

    def finish_stage(self, now: datetime, position_id: str, stage: Purpose, status: str,
                     breakeven: float | None = None) -> None:
        with self.tx():
            self.conn.execute("UPDATE exit_stages SET status=?, updated_at=? WHERE position_id=? AND stage=?",
                              (status, _iso(now), position_id, str(stage)))
            if status == "DONE" and stage is Purpose.TP1:
                self.conn.execute("UPDATE positions SET tp1_done=1, breakeven_stop=? WHERE position_id=?",
                                  (breakeven, position_id))
            if status == "DONE" and stage is Purpose.TP2:
                self.conn.execute("UPDATE positions SET tp2_done=1 WHERE position_id=?", (position_id,))
            self.audit(now, "STAGE_FINISH", position_id=position_id, stage=str(stage), status=status)

    def active_intents_for_position(self, position_id: str) -> list[sqlite3.Row]:
        return [r for r in self.active_intents() if r["position_id"] == position_id]

    # ---------------- reporting helpers ----------------
    def fills_for_day(self, day: str) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM fills WHERE trading_day=? ORDER BY time", (day,)).fetchall()

    def audit_for_day(self, day_start_iso: str, day_end_iso: str, kinds: tuple[str, ...]) -> list[sqlite3.Row]:
        marks = ",".join("?" for _ in kinds)
        return self.conn.execute(
            f"SELECT * FROM audit WHERE ts>=? AND ts<? AND kind IN ({marks}) ORDER BY id",
            (day_start_iso, day_end_iso, *kinds)).fetchall()
