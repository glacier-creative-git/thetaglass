"""Backfill real underlying price history from the broker into the store.

We can't recover historical *option* marks (no broker serves them), but the *underlying*
daily bars are real and freely available — so we fetch them once per symbol from before
the position opened. That history feeds two things: the real underlying price line, and
realized volatility (RV) for the IV-vs-RV view.

Idempotent: bars upsert per (symbol, day), so re-running only fills gaps / the latest bar.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from thetaglass.broker.base import Broker
from thetaglass.store import Store

# Pull this many calendar days before a position opened, so a ~20-trading-day RV window
# is already populated at the position's open.
RV_LOOKBACK_DAYS = 45


def _open_date(opened_at: str | None) -> date:
    if not opened_at:
        return date.today() - timedelta(days=RV_LOOKBACK_DAYS)
    return datetime.fromisoformat(opened_at.replace("Z", "+00:00")).date()


def backfill_symbol(broker: Broker, store: Store, symbol: str, since: date) -> int:
    """Fetch daily bars for `symbol` from `since` → now and upsert them. Returns count."""
    start = since.isoformat() + "T00:00:00Z"
    bars = broker.get_equity_historicals(symbol, start, "day")
    return store.upsert_equity_bars(symbol, bars)


def backfill_for_positions(broker: Broker, store: Store, positions) -> dict[str, int]:
    """Backfill each distinct underlying behind the given positions (Position objects).

    Looks back to RV_LOOKBACK_DAYS before each position opened. One broker call per
    distinct symbol; safe to call every tick (upsert is idempotent and cheap).
    """
    out: dict[str, int] = {}
    for p in positions:
        symbol = p.underlying
        if symbol in out:
            continue
        since = _open_date(p.opened_at) - timedelta(days=RV_LOOKBACK_DAYS)
        out[symbol] = backfill_symbol(broker, store, symbol, since)
    return out
