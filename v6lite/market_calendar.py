"""US equity session calendar (dependency-free).

Holiday rules are ported from trading_bot_v6/core/market_calendar.py.
Early-close days (13:00 ET) are added so entry cut-offs stay correct.
All scheduling is done in America/New_York (fixes the legacy SCANNER-TZ-1
class of bugs where PT wall-clock times were used for ET market events).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)
EARLY_CLOSE = time(13, 0)


def _observed_fixed(year: int, month: int, day: int) -> date:
    actual = date(year, month, day)
    if actual.weekday() == 5:
        return actual - timedelta(days=1)
    if actual.weekday() == 6:
        return actual + timedelta(days=1)
    return actual


def _nth_weekday(year: int, month: int, weekday: int, nth: int) -> date:
    first = date(year, month, 1)
    delta = (weekday - first.weekday()) % 7
    return first + timedelta(days=delta + 7 * (nth - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    last = date(year, 12, 31) if month == 12 else date(year, month + 1, 1) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _easter(year: int) -> date:
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def holidays(year: int) -> dict[date, str]:
    out: dict[date, str] = {}
    for y in (year - 1, year, year + 1):
        out[_observed_fixed(y, 1, 1)] = "New Year's Day"
        out[_nth_weekday(y, 1, 0, 3)] = "Martin Luther King Jr. Day"
        out[_nth_weekday(y, 2, 0, 3)] = "Presidents' Day"
        out[_easter(y) - timedelta(days=2)] = "Good Friday"
        out[_last_weekday(y, 5, 0)] = "Memorial Day"
        if y >= 2022:
            out[_observed_fixed(y, 6, 19)] = "Juneteenth"
        out[_observed_fixed(y, 7, 4)] = "Independence Day"
        out[_nth_weekday(y, 9, 0, 1)] = "Labor Day"
        out[_nth_weekday(y, 11, 3, 4)] = "Thanksgiving Day"
        out[_observed_fixed(y, 12, 25)] = "Christmas Day"
    return {d: n for d, n in out.items() if d.year == year}


def early_closes(year: int) -> set[date]:
    days: set[date] = set()
    black_friday = _nth_weekday(year, 11, 3, 4) + timedelta(days=1)
    days.add(black_friday)
    for candidate in (date(year, 7, 3), date(year, 12, 24)):
        if candidate.weekday() < 5:
            days.add(candidate)
    hol = holidays(year)
    return {d for d in days if d not in hol}


def trading_day(now_utc: datetime) -> date:
    return now_utc.astimezone(ET).date()


def is_trading_day(day: date) -> bool:
    return day.weekday() < 5 and day not in holidays(day.year)


@dataclass(frozen=True)
class Session:
    day: date
    open_utc: datetime
    close_utc: datetime


def session_for(day: date) -> Session | None:
    if not is_trading_day(day):
        return None
    close_t = EARLY_CLOSE if day in early_closes(day.year) else REGULAR_CLOSE
    open_dt = datetime.combine(day, REGULAR_OPEN, tzinfo=ET).astimezone(timezone.utc)
    close_dt = datetime.combine(day, close_t, tzinfo=ET).astimezone(timezone.utc)
    return Session(day, open_dt, close_dt)


def is_rth(now_utc: datetime) -> bool:
    session = session_for(trading_day(now_utc))
    return bool(session and session.open_utc <= now_utc < session.close_utc)


def et_time_on(day: date, hhmm: str) -> datetime:
    hour, minute = (int(part) for part in hhmm.split(":"))
    return datetime.combine(day, time(hour, minute), tzinfo=ET).astimezone(timezone.utc)
