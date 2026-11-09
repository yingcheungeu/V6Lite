"""Process-local observation compression; never trading evidence or state."""
from __future__ import annotations
import copy
import json
from datetime import timedelta


class ObservationAudit:
    def __init__(self):
        self.prices = {}
        self.pending = {}
        self.windows = {}
        self.generation = None
        self.latest = None

    def clone(self):
        return copy.deepcopy(self)

    def _time(self, now):
        self.latest = max(now, self.latest) if self.latest is not None else now

    def flush(self, write, now, *, position_id=None, reason="periodic"):
        for pid in list(self.pending):
            if position_id is not None and pid != position_id:
                continue
            items = self.pending[pid]
            if items:
                write(now, "OBSERVATION_SUMMARY", position_id=pid, reason=reason,
                      observations=[dict(item) for item in items.values()])
                # Only the candidate is changed after the INSERT succeeds.
                del self.pending[pid]
                self.windows[pid] = now
        self._time(now)

    def due(self, now):
        return [pid for pid, items in self.pending.items()
                if items and now - self.windows[pid] >= timedelta(minutes=30)]

    def flush_due(self, write, now):
        for pid in self.due(now):
            self.flush(write, now, position_id=pid)

    def change_generation(self, write, now, generation):
        if generation is None:
            return
        if self.generation is not None and generation != self.generation:
            self.flush(write, now, reason="connection_generation")
            self.prices.clear()
        self.generation = generation

    def suppress(self, write, now, kind, payload):
        pid = payload.get("position_id")
        opened = self.windows.setdefault(pid, now)
        if now - opened >= timedelta(minutes=30):
            self.flush(write, now, position_id=pid)
            self.windows[pid] = now
        key = (kind, payload.get("intent_id"))
        items = self.pending.setdefault(pid, {})
        item = items.setdefault(key, {"kind": kind, "position_id": pid,
            "intent_id": payload.get("intent_id"), "count": 0,
            "first_at": now.isoformat(), "last_at": now.isoformat()})
        item["count"] += 1
        item["last_at"] = now.isoformat()
        self._time(now)

    def observe(self, write, now, kind, payload, *, generation=None, changed=None):
        self.change_generation(write, now, generation)
        if kind == "ORDER_PRICE_OBSERVATION":
            key = payload["intent_id"]
            encoded = json.dumps(payload, sort_keys=True, default=str)
            must_write = (key not in self.prices or self.prices[key] != encoded or
                          bool(payload.get("error_code") or payload.get("reason")))
            if must_write:
                write(now, kind, **payload)
                self.prices[key] = encoded
            else:
                self.suppress(write, now, kind, payload)
        elif kind == "PROTECT_STATE":
            if changed:
                write(now, kind, **payload)
            else:
                self.suppress(write, now, kind, payload)
        else:
            raise ValueError("not an observation-compressible audit kind")
        self._time(now)
