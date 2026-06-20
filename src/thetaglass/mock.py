"""Synthetic positions + history for development and the monitor demo.

Real books often hold a single position (or none), but the monitor's whole point is
arrow-key selection *between* positions. So we fabricate believable extras — a position
dict shaped exactly like positions_current, plus a snapshot history shaped exactly like
the snapshots table — and tag them MOCK so they can never be mistaken for live data.

The generated history is deterministic (seeded) so screenshots and tests are stable.
"""
from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone

from thetaglass.state import compute
from thetaglass.state.baseline import expected_pl_pct
from thetaglass.state.blackscholes import R, bs_price, spread_cost_to_close
from thetaglass.state.models import Leg, Position

MOCK_PREFIX = "MOCK"


def _history_for(pos: Position, opened: datetime, now: datetime, *,
                 spot_open: float, spot_drift: float, vol: float, seed: int,
                 start_day: int = 0) -> list[dict]:
    """Walk day-by-day from open to now, producing snapshot rows like the store's.

    The underlying random-walks with a drift toward (or away from) the short strike, and
    P/L is recomputed from a value that decays along the √time curve plus noise — so the
    realized line looks like a real, slightly noisy decay path. `start_day` > 0 simulates
    only starting to watch a few days after open (so the chart shows a backfill bridge).
    """
    rng = random.Random(seed)
    short = pos.short_leg
    base_iv = short.iv or 0.25
    legs_d = [{"side": l.side, "option_type": l.option_type, "strike": l.strike,
               "quantity": l.quantity} for l in pos.legs]
    credit, mp = pos.credit_received, pos.max_profit
    rows: list[dict] = []
    days = max(1, (now.date() - opened.date()).days)
    spot = spot_open
    iv = base_iv
    for d in range(days + 1):
        spot += spot_drift + rng.uniform(-vol, vol)
        # IV drifts (mean-reverting wander) so the IV line isn't a flat demo artifact.
        iv += (base_iv - iv) * 0.15 + rng.uniform(-0.012, 0.012)
        if d < start_day:
            continue
        dte_remaining = pos.dte_at_open - d
        exp_pct = expected_pl_pct(dte_remaining, pos.dte_at_open)
        # realized P/L is the REAL spread value at this price/time (BS), so the P/L cell
        # agrees with where the price sits in the underlying cell's profit-edge cone.
        cost = spread_cost_to_close(legs_d, spot, max(0.0, dte_remaining) / 365.0, max(0.05, iv))
        pl_dollars = round(credit - cost, 2)
        pl_pct = pl_dollars / mp if mp else 0.0
        dist = compute.distance_to_short_strike_pct(short, spot)
        rows.append({
            "tick_at": (opened + timedelta(days=d)).isoformat(),
            "is_daily_close": 1,
            "dte_remaining": dte_remaining,
            "underlying_price": round(spot, 2),
            "current_value": round(cost, 2),
            "pl_dollars": pl_dollars,
            "pl_pct_of_max_profit": round(pl_pct, 4),
            "expected_pl_pct": round(exp_pct, 4),
            "health_score": None,
            "net_delta": None, "net_gamma": None, "net_theta": None, "net_vega": None,
            "iv_now": round(max(0.05, iv), 4),
            "iv_regime_delta_pct": 0.0,
            "distance_to_short_strike_pct": round(dist, 4) if dist is not None else None,
            "snapshot_json": None,
        })
    return rows


def closes_from_history(history: list[dict]) -> list[tuple[str, float]]:
    """(date, close) series from snapshot underlying_price — the RV input when real
    backfilled bars aren't available (mock positions, or a freshly-watched real one)."""
    out = []
    for h in history:
        px = h.get("underlying_price")
        if px is not None:
            out.append((h["tick_at"][:10], px))
    return out


def make_mock_position(now: datetime | None = None, *, symbol: str = "SPY",
                       short_k: float = 665, long_k: float = 660, spot_open: float = 678.0,
                       drift: float = -0.7, vol: float = 1.4, seed: int = 42,
                       days_open: int = 18, dte_at_open: int = 45,
                       short_iv: float = 0.196, synced_after: int = 3) -> tuple[dict, list[dict]]:
    """A believable put credit spread. Returns (position_dict, history_rows) shaped
    exactly like positions_current / snapshots so views can't tell it from live data."""
    now = now or datetime.now(timezone.utc)
    opened = now - timedelta(days=days_open)
    exp = (opened + timedelta(days=dte_at_open)).date().isoformat()

    # Price the entry fills with BS at open, so credit = the real spread value and the
    # BS-priced P/L starts at ~0 (rather than an artificial day-1 loss from a mismatched
    # hardcoded credit).
    t0 = dte_at_open / 365.0
    short_fill = bs_price("put", spot_open, short_k, t0, R, short_iv)
    long_fill = bs_price("put", spot_open, long_k, t0, R, short_iv + 0.01)
    short = Leg(option_id=f"MOCK-{symbol}-S", side="short", option_type="put", strike=short_k,
                quantity=1, expiration=exp, average_price=-round(short_fill * 100, 2),
                mark=round(short_fill, 4), iv=short_iv,
                delta=-0.31, gamma=0.006, theta=-0.21, vega=0.62)
    long = Leg(option_id=f"MOCK-{symbol}-L", side="long", option_type="put", strike=long_k,
               quantity=1, expiration=exp, average_price=round(long_fill * 100, 2),
               mark=round(long_fill, 4), iv=short_iv + 0.01,
               delta=-0.24, gamma=0.005, theta=-0.18, vega=0.55)

    pos = Position(position_id=f"{MOCK_PREFIX}-{symbol}-{short_k:g}-{long_k:g}",
                   account_number=MOCK_PREFIX, underlying=symbol,
                   strategy_type="put_credit_spread", legs=[short, long])
    base = compute.freeze_baseline(pos.legs, opened.isoformat(), dte_at_open)
    for k, v in base.items():
        setattr(pos, k, v)
    pos.iv_at_entry = short.iv

    hist = _history_for(pos, opened, now, spot_open=spot_open, spot_drift=drift,
                        vol=vol, seed=seed, start_day=synced_after)
    last = hist[-1]
    # price each leg via BS at the final state so the pos SUMMARY (Greeks-free) matches the
    # BS-priced history rows — otherwise recompute_live uses the static creation marks.
    t_final = max(0.0, last["dte_remaining"]) / 365.0
    for l in pos.legs:
        l.iv = last["iv_now"]
        l.mark = round(bs_price(l.option_type, last["underlying_price"], l.strike,
                                t_final, R, last["iv_now"]), 4)
    compute.recompute_live(pos, last["underlying_price"], last["dte_remaining"])
    d = pos.to_dict()
    d["is_mock"] = True
    return d, hist


# Preset variants so the monitor (and tests) can show a multi-position book.
_BOOK_PRESETS = [
    # a winning put credit spread: SPY rallied up into the 50–90% profit-taking band,
    # so the price line sits between the grey (50%) and green (90%) edges of the cone.
    dict(symbol="SPY", short_k=665, long_k=660, spot_open=694.0, drift=2.0, seed=42,
         synced_after=3),
    dict(symbol="IWM", short_k=205, long_k=200, spot_open=214.0, drift=0.25, seed=7,
         days_open=9, dte_at_open=38, short_iv=0.232, synced_after=2),
    dict(symbol="NVDA", short_k=118, long_k=115, spot_open=131.0, drift=-0.55, seed=99,
         days_open=25, dte_at_open=30, short_iv=0.41, synced_after=6),
    dict(symbol="AAPL", short_k=245, long_k=240, spot_open=252.0, drift=0.3, seed=11,
         days_open=12, dte_at_open=40, short_iv=0.28, synced_after=2),
    dict(symbol="TSLA", short_k=400, long_k=390, spot_open=412.0, drift=-1.5, seed=23,
         days_open=22, dte_at_open=45, short_iv=0.55, synced_after=5),
    dict(symbol="AMD", short_k=165, long_k=160, spot_open=171.0, drift=0.4, seed=31,
         days_open=8, dte_at_open=30, short_iv=0.45, synced_after=1),
]


def make_mock_book(count: int = 1,
                   now: datetime | None = None) -> list[tuple[dict, list[dict], list[tuple[str, float]]]]:
    """`count` distinct mock (position, history, closes) entries for the monitor / tests.
    closes are derived from the position's own synthetic price walk (RV input)."""
    out = []
    for i in range(count):
        pos, hist = make_mock_position(now, **_BOOK_PRESETS[i % len(_BOOK_PRESETS)])
        out.append((pos, hist, closes_from_history(hist)))
    return out
