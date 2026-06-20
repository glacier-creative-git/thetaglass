"""Regression test: the documented QQQ 729/727 numbers must hold.

These assertions are the worked example in docs/STATE_MACHINE.md. If the health math
or baseline ever drifts, this fails loudly.
"""
from datetime import date

from thetaglass.state import compute
from thetaglass.state.baseline import expected_pl_pct
from thetaglass.state.models import Leg, Position


def _qqq_spread() -> Position:
    long = Leg(option_id="L", side="long", option_type="put", strike=727, quantity=2,
               expiration="2026-07-17", average_price=1738.0,
               mark=13.865, iv=0.252798, delta=-0.374689, gamma=0.007408,
               theta=-0.323813, vega=0.773281)
    short = Leg(option_id="S", side="short", option_type="put", strike=729, quantity=2,
                expiration="2026-07-17", average_price=-1813.0,
                mark=14.520, iv=0.250563, delta=-0.389120, gamma=0.007605,
                theta=-0.323686, vega=0.782091)
    pos = Position(position_id="x", account_number="a", underlying="QQQ",
                   strategy_type="put_credit_spread", legs=[long, short])
    base = compute.freeze_baseline(pos.legs, "2026-06-17T15:06:36Z", 30)
    for k, v in base.items():
        setattr(pos, k, v)
    pos.iv_at_entry = short.iv  # first sighting
    return pos


def test_frozen_baseline():
    pos = _qqq_spread()
    assert pos.credit_received == 150.0
    assert pos.max_profit == 150.0
    assert pos.max_loss == 250.0


def test_live_and_health():
    pos = _qqq_spread()
    compute.recompute_live(pos, spot=739.80, dte_remaining=28)

    assert pos.current_value == 131.0
    assert pos.pl_dollars == 19.0
    assert round(pos.pl_pct_of_max_profit, 3) == 0.127
    assert round(pos.distance_to_short_strike_pct, 4) == 0.0146
    # ahead of schedule → theta axis caps at 1.0; near-money cushion drags strike axis
    assert pos.health_axes["theta_on_track"] == 1.0
    assert round(pos.health_axes["strike_distance"], 3) == 0.487
    assert pos.health_axes["iv_stability"] == 1.0
    assert round(pos.health_score, 2) == 0.79


def test_baseline_curve_is_backloaded():
    # √time: slow early, fast near expiry (vs a straight line)
    assert round(expected_pl_pct(28, 30), 3) == 0.034
    assert round(expected_pl_pct(15, 30), 3) == 0.293
    assert round(expected_pl_pct(2, 30), 3) == 0.742
    assert expected_pl_pct(0, 30) == 1.0


def test_strike_breach_floors_health():
    """The weakest-link rule: at the short strike, health collapses despite other axes."""
    pos = _qqq_spread()
    compute.recompute_live(pos, spot=729.0, dte_remaining=28)  # spot == short strike
    assert pos.distance_to_short_strike_pct == 0.0
    assert pos.health_axes["strike_distance"] == 0.0
    assert pos.health_score == 0.0  # not the ~0.6 a plain average would give
