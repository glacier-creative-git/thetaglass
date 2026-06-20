"""Per-tick derivations (Layer A 'DERIVED' fields + the frozen baseline at open).

Pure functions over a list of Legs. No network, no broker vocabulary. v1 computes
defined-risk verticals (two-leg credit/debit spreads) precisely; other shapes get
best-effort values and are flagged by strategy_type upstream.
"""
from __future__ import annotations

from thetaglass.state.baseline import expected_pl_pct
from thetaglass.state.health import compute_axes, health_score
from thetaglass.state.models import Leg, Position

MULT = 100  # standard equity-option contract multiplier (shares per contract)


def credit_received(legs: list[Leg]) -> float:
    """Net cash taken in at open. Short legs carry negative average_price (a credit),
    long legs positive (a debit); average_price is already per-contract dollars."""
    return -sum(l.average_price * l.quantity for l in legs)


def spread_width_dollars(legs: list[Leg]) -> float:
    """Strike width × multiplier × quantity, for a two-leg vertical."""
    if len(legs) != 2:
        return 0.0
    qty = legs[0].quantity
    return abs(legs[0].strike - legs[1].strike) * MULT * qty


def current_value(legs: list[Leg]) -> float | None:
    """Cost to close right now: buy back shorts (pay), sell longs (receive)."""
    if any(l.mark is None for l in legs):
        return None
    total = 0.0
    for l in legs:
        sign = 1.0 if l.side == "short" else -1.0   # short = pay to buy back
        total += sign * l.mark * MULT * l.quantity
    return total


def net_greek(legs: list[Leg], name: str) -> float | None:
    """Position-level Greek: long adds, short subtracts, scaled by qty × multiplier."""
    vals = [getattr(l, name) for l in legs]
    if any(v is None for v in vals):
        return None
    total = 0.0
    for l in legs:
        sign = 1.0 if l.side == "long" else -1.0
        total += sign * getattr(l, name) * l.quantity * MULT
    return total


def distance_to_short_strike_pct(short: Leg, spot: float) -> float | None:
    """Cushion between spot and the short strike, in the position's danger direction.
    Puts: danger is price falling below the short strike. Calls: rising above it."""
    if spot is None or short is None or spot <= 0:
        return None
    if short.option_type == "put":
        return (spot - short.strike) / spot
    return (short.strike - spot) / spot


def freeze_baseline(legs: list[Leg], opened_at: str, dte_at_open: int) -> dict:
    """The FROZEN entry facts — computed once when a position is first seen."""
    credit = credit_received(legs)
    width = spread_width_dollars(legs)
    # Defined-risk vertical: max loss is the width you can't recover, minus the credit.
    max_loss = max(0.0, width - credit) if width else None
    return {
        "opened_at": opened_at,
        "dte_at_open": dte_at_open,
        "credit_received": round(credit, 2),
        "max_profit": round(credit, 2),
        "max_loss": round(max_loss, 2) if max_loss is not None else None,
    }


def recompute_live(pos: Position, spot: float, dte_remaining: int) -> Position:
    """Fill the LIVE + DERIVED fields on an already-baselined Position."""
    pos.underlying_price = spot
    pos.dte_remaining = dte_remaining

    cv = current_value(pos.legs)
    pos.current_value = round(cv, 2) if cv is not None else None
    if cv is not None and pos.credit_received is not None:
        pos.pl_dollars = round(pos.credit_received - cv, 2)
        if pos.max_profit:
            pos.pl_pct_of_max_profit = round(pos.pl_dollars / pos.max_profit, 4)

    pos.expected_pl_pct = round(expected_pl_pct(dte_remaining, pos.dte_at_open), 4)

    pos.net_delta = _r(net_greek(pos.legs, "delta"))
    pos.net_gamma = _r(net_greek(pos.legs, "gamma"))
    pos.net_theta = _r(net_greek(pos.legs, "theta"))
    pos.net_vega = _r(net_greek(pos.legs, "vega"))

    short = pos.short_leg
    pos.iv_now = short.iv if short else None
    pos.distance_to_short_strike_pct = _r(distance_to_short_strike_pct(short, spot))

    if pos.iv_at_entry and pos.iv_now:
        pos.iv_regime_delta_pct = round((pos.iv_now - pos.iv_at_entry) / pos.iv_at_entry, 4)
    else:
        pos.iv_regime_delta_pct = 0.0

    # health needs all three inputs present
    if None not in (pos.pl_pct_of_max_profit, pos.expected_pl_pct,
                    pos.distance_to_short_strike_pct):
        axes = compute_axes(
            pos.pl_pct_of_max_profit, pos.expected_pl_pct,
            pos.distance_to_short_strike_pct, pos.iv_regime_delta_pct or 0.0)
        pos.health_axes = {k: round(v, 4) for k, v in axes.items()}
        pos.health_score = health_score(axes)
    return pos


def _r(v: float | None, n: int = 4) -> float | None:
    return round(v, n) if v is not None else None
