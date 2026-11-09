"""Sanitized, read-only cross-check against legacy Paper exit ledger.

Only whitelisted non-secret price/plan fields leave the ledger.  An exit
record's timestamp is not treated as the original entry timestamp.
"""

from __future__ import annotations

import csv
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from v6lite.market_calendar import is_trading_day

ROOT = Path(__file__).resolve().parents[2]
LEDGER = Path(r"C:\Dev\TradingV6\state\tpsl_exit_ledger.json")
OUTPUT = Path(__file__).resolve().parent / "out"
SYMBOLS = ("GOOGL", "SPY", "TSLA", "IWM", "PANW", "IBB", "ABT")
ET = ZoneInfo("America/New_York")


def _et_day(iso: str) -> date:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(ET).date()


def _nearby(day: date) -> set[str]:
    out = {day.isoformat()}
    for direction in (-1, 1):
        cursor = day
        for _ in range(3):
            cursor += timedelta(days=direction)
            while not is_trading_day(cursor):
                cursor += timedelta(days=direction)
            out.add(cursor.isoformat())
    return out


def build() -> list[dict]:
    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
    with (OUTPUT / "A_signals.csv").open(encoding="utf-8", newline="") as f:
        signals = list(csv.DictReader(f))
    with (OUTPUT / "A_trades.csv").open(encoding="utf-8", newline="") as f:
        trades = list(csv.DictReader(f))
    events: list[dict] = []
    for symbol in SYMBOLS:
        plan_record = ledger.get("price_plans", {}).get(symbol, {}).get("active")
        plan = None
        if isinstance(plan_record, dict):
            plan = {field: plan_record.get(field) for field in
                    ("entry_price", "atr", "stop", "tp1", "tp2", "tp3")}
            plan_day = _et_day(plan_record["created_at_utc"])
            if date(2026, 4, 30) <= plan_day <= date(2026, 8, 19):
                events.append({"symbol": symbol, "event_day": plan_day.isoformat(),
                               "event_type": "latest_plan_created_or_migrated",
                               "old_entry_price": plan["entry_price"], "old_plan": plan})
        for exit_record in ledger.get("exits", {}).values():
            if exit_record.get("symbol") != symbol or not exit_record.get("created_at"):
                continue
            event_day = _et_day(exit_record["created_at"])
            if date(2026, 4, 30) <= event_day <= date(2026, 8, 19):
                events.append({"symbol": symbol, "event_day": event_day.isoformat(),
                               "event_type": "exit_" + str(exit_record.get("stage", "")),
                               "old_entry_price": exit_record.get("entry_price"),
                               "old_plan": plan if plan and plan_day <= event_day else None})
    result = []
    for event in sorted(events, key=lambda e: (e["event_day"], e["symbol"], e["event_type"])):
        nearby = _nearby(date.fromisoformat(event["event_day"]))
        event["a_signals_within_3_sessions"] = [
            {field: s[field] for field in ("day", "score", "signal_price", "atr", "vix")}
            for s in signals if s["symbol"] == event["symbol"] and s["day"] in nearby]
        event["a_entries_within_3_sessions"] = [
            {field: t[field] for field in ("entry_day", "entry_price", "atr", "stop", "tp1", "tp2", "tp3")}
            for t in trades if t["symbol"] == event["symbol"] and t["entry_day"] in nearby]
        result.append(event)
    return result


def main() -> None:
    result = build()
    (OUTPUT / "paper_crosscheck.json").write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(f"Paper event anchors: {len(result)}")


if __name__ == "__main__":
    main()
