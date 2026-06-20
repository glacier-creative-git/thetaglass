"""The market clock — pure, testable session logic with no loop and no I/O.

Answers three questions the daemon needs: is the market open right now, is *this* the
last tick of the session (so the snapshot should be tagged daily-close), and how long do
we nap when it's shut. Kept side-effect-free so the session math can be unit-tested
without waiting for 9:30am.

v1 models the regular weekday session only. US market holidays are a known gap: on a
holiday this returns "open" and the daemon will sync a stale book harmlessly. A holiday
calendar is a clean later add (a date set checked in `is_open`).
"""
from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from thetaglass.settings import CONFIG


def _parse_hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


class MarketClock:
    def __init__(self, tz: str | None = None, open_t: str | None = None,
                 close_t: str | None = None):
        self.tz = ZoneInfo(tz or CONFIG.MARKET_TZ)
        self.open_t = _parse_hhmm(open_t or CONFIG.MARKET_OPEN)
        self.close_t = _parse_hhmm(close_t or CONFIG.MARKET_CLOSE)

    def now(self) -> datetime:
        return datetime.now(self.tz)

    def is_open(self, dt: datetime) -> bool:
        """Mon–Fri within [open, close). Holidays not modeled (see module docstring)."""
        if dt.weekday() >= 5:                       # 5=Sat, 6=Sun
            return False
        return self.open_t <= dt.timetz().replace(tzinfo=None) < self.close_t

    def is_session_close_tick(self, dt: datetime, interval_seconds: int) -> bool:
        """True if the next scheduled beat would land at/after today's close — i.e. this
        is the final sync of the session, the one worth a full daily-close snapshot."""
        if not self.is_open(dt):
            return False
        nxt = dt + timedelta(seconds=interval_seconds)
        return nxt.date() != dt.date() or nxt.timetz().replace(tzinfo=None) >= self.close_t

    def seconds_until_open(self, dt: datetime) -> float:
        """Seconds from `dt` to the next session open (0 if already open)."""
        if self.is_open(dt):
            return 0.0
        candidate = dt.replace(hour=self.open_t.hour, minute=self.open_t.minute,
                               second=0, microsecond=0)
        if candidate <= dt:                          # past today's open → start tomorrow
            candidate += timedelta(days=1)
        while candidate.weekday() >= 5:              # skip the weekend
            candidate += timedelta(days=1)
        return (candidate - dt).total_seconds()
