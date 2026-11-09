"""Injectable clocks. All engine time is timezone-aware UTC."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FixedClock:
    """Deterministic clock for tests and replays."""

    def __init__(self, start: datetime):
        if start.tzinfo is None:
            raise ValueError("FixedClock requires a timezone-aware datetime")
        self._now = start.astimezone(timezone.utc)

    def now(self) -> datetime:
        return self._now

    def set(self, value: datetime) -> None:
        if value.tzinfo is None:
            raise ValueError("FixedClock requires a timezone-aware datetime")
        self._now = value.astimezone(timezone.utc)

    def advance(self, **kwargs) -> datetime:
        self._now = self._now + timedelta(**kwargs)
        return self._now
