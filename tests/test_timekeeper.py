"""Market-clock session logic — provable without waiting for the opening bell.

2026-06-15 is a Monday, 2026-06-19 a Friday, 2026-06-20 a Saturday (see dates below).
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from thetaglass.timekeeper.clock import MarketClock

ET = ZoneInfo("America/New_York")


def _et(y, m, d, hh, mm) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=ET)


@pytest.fixture()
def clock():
    return MarketClock()  # defaults: 09:30–16:00 America/New_York


def test_is_open_regular_session(clock):
    assert clock.is_open(_et(2026, 6, 15, 10, 0))      # Mon 10:00 → open
    assert not clock.is_open(_et(2026, 6, 15, 9, 0))   # before the bell
    assert not clock.is_open(_et(2026, 6, 15, 16, 0))  # close is exclusive
    assert clock.is_open(_et(2026, 6, 15, 15, 59))     # last minute → open


def test_weekend_is_closed(clock):
    assert not clock.is_open(_et(2026, 6, 20, 10, 0))  # Saturday, mid-"session"


def test_session_close_tick(clock):
    # 15:58 + 5min beat lands at 16:03 ≥ close → this is the daily-close tick
    assert clock.is_session_close_tick(_et(2026, 6, 15, 15, 58), 300)
    # mid-session, the next beat is still inside the session → not the close
    assert not clock.is_session_close_tick(_et(2026, 6, 15, 10, 0), 300)
    # when the market is shut there's no close tick at all
    assert not clock.is_session_close_tick(_et(2026, 6, 20, 10, 0), 300)


def test_seconds_until_open_same_day(clock):
    secs = clock.seconds_until_open(_et(2026, 6, 15, 8, 0))  # Mon pre-market
    assert secs == 90 * 60                                   # 08:00 → 09:30 = 90 min


def test_seconds_until_open_skips_weekend(clock):
    now = _et(2026, 6, 19, 17, 0)                  # Friday after close
    target = now + timedelta(seconds=clock.seconds_until_open(now))
    assert target.weekday() == 0                   # lands on Monday
    assert (target.hour, target.minute) == (9, 30)
    assert clock.is_open(target)


def test_seconds_until_open_zero_when_open(clock):
    assert clock.seconds_until_open(_et(2026, 6, 15, 10, 0)) == 0.0
