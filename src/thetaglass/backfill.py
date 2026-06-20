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


def entry_iv_for_position(broker: Broker, pos: dict) -> float | None:
    """Reconstruct a position's true entry IV: find the underlying's price at the actual
    fill moment (intraday hourly bar nearest opened_at) and BS-invert the short leg's fill.

    Uses intraday — not the daily close — because a position opened mid-session on a volatile
    day can sit far from that day's close, which would throw the implied IV way off.
    """
    from thetaglass.state.blackscholes import implied_entry_iv

    short = next((l for l in pos.get("legs", []) if l.get("side") == "short"), None)
    if not short or not pos.get("opened_at") or not pos.get("dte_at_open"):
        return None
    opened = datetime.fromisoformat(pos["opened_at"].replace("Z", "+00:00"))
    day = opened.date()
    bars = broker.get_equity_historicals(
        pos["underlying"], day.isoformat() + "T00:00:00Z", "hour",
        end_time=(day + timedelta(days=1)).isoformat() + "T00:00:00Z")
    cand = []
    for b in bars:
        c, ts = b.get("close_price"), b.get("begins_at")
        if c and ts:
            t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if t.date() == day:
                cand.append((t, float(c)))
    if not cand:
        return None
    s_entry = min(cand, key=lambda x: abs((x[0] - opened).total_seconds()))[1]
    fill = abs(short.get("average_price") or 0.0) / 100.0     # per-contract $ → per share
    if fill <= 0:
        return None
    return implied_entry_iv(short["option_type"], fill, s_entry, short["strike"],
                            pos["dte_at_open"])


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
