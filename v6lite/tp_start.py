"""Opening barrier for NEW TP stages; never an order-send or recovery gate."""
from datetime import datetime, timedelta, timezone
import math

from v6lite.market_calendar import session_for, trading_day
from v6lite.models import Purpose

TP_STAGES = {Purpose.TP1, Purpose.TP2, Purpose.TP3}


def tp_start_deadline(cfg, now):
    """Return the current session's deadline only while new TP starts wait."""
    seconds = cfg.tp_start_after_open_seconds
    if seconds == 0:
        return None
    session = session_for(trading_day(now))
    if session is None:
        return None
    try:
        deadline = session.open_utc + timedelta(seconds=seconds)
    except OverflowError:
        # Every nonnegative integer is valid. A deadline beyond datetime's
        # representable range still means wait, without crashing protection.
        deadline = datetime.max.replace(tzinfo=timezone.utc)
    return deadline if now < deadline else None


class TpStartMixin:
    def _tp_start_deferred(self, now, position_id, stage, *, quote=None, trigger_level=None):
        if stage not in TP_STAGES:
            return False
        deadline = tp_start_deadline(self.cfg, now)
        if deadline is None:
            return False
        pos = self.store.position(position_id)
        if pos is None:
            return True
        day = trading_day(now).isoformat()
        # Durable dedup survives restarts, has no workflow/reservation/retry
        # side effect, and never sends a phone card.
        with self.store.tx():
            prior = self.store.conn.execute(
                "SELECT 1 FROM audit WHERE kind='TP_START_DEFERRED_OPEN' "
                "AND json_extract(payload,'$.position_id')=? "
                "AND json_extract(payload,'$.trading_day')=? LIMIT 1",
                (position_id, day)).fetchone()
            if prior is None:
                quote = quote if quote is not None else self.broker.quote(pos['symbol'])
                level = trigger_level if trigger_level is not None else pos[str(stage).lower()]
                def finite(value):
                    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None
                self.store.audit(now, 'TP_START_DEFERRED_OPEN', position_id=position_id,
                    symbol=pos['symbol'], trading_day=day, stage=str(stage), trigger_level=level,
                    bid=finite(quote.bid) if quote else None, ask=finite(quote.ask) if quote else None,
                    last=finite(quote.last) if quote else None, mark=finite(quote.mark()) if quote else None,
                    bid_raw=str(quote.bid) if quote else 'missing',
                    ask_raw=str(quote.ask) if quote else 'missing',
                    last_raw=str(quote.last) if quote else 'missing',
                    quote_time=quote.ts.isoformat() if quote and quote.ts else None,
                    last_time=quote.last_ts.isoformat() if quote and quote.last_ts else None,
                    scheduled_start=deadline.isoformat(),
                    tp_start_after_open_seconds=self.cfg.tp_start_after_open_seconds)
        return True
