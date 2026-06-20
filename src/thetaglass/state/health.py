"""The health score (Layer D2).

A single 0–1 number from three axes (each 0 bad → 1 good). The important bit is the
*weakest-link floor*: a plain weighted average would let one catastrophic axis (e.g.
price about to breach your short strike) get hidden behind two healthy ones. So once any
axis goes critical, health can't rise above it.
"""
from __future__ import annotations

from thetaglass.settings import CONFIG


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def compute_axes(
    pl_pct_of_max_profit: float,
    expected_pl_pct: float,
    distance_to_short_strike_pct: float,
    iv_regime_delta_pct: float,
) -> dict:
    """The three 0–1 health axes."""
    # theta_on_track: actual vs expected progress. Ahead-of-schedule caps at 1.0.
    if expected_pl_pct <= 0:
        theta_on_track = 1.0 if pl_pct_of_max_profit >= 0 else 0.0
    else:
        theta_on_track = _clamp(pl_pct_of_max_profit / expected_pl_pct, 0.0, 1.5)
    theta_on_track = min(theta_on_track, 1.0)

    # strike_distance: how much of the "safe" cushion remains. 1.0 when far, 0 at strike.
    strike_distance = _clamp(
        distance_to_short_strike_pct / CONFIG.BREACH_THRESHOLD_PCT, 0.0, 1.0)

    # iv_stability: 1.0 when IV unchanged from entry, → 0 as it approaches the alarm level.
    iv_stability = _clamp(
        1.0 - abs(iv_regime_delta_pct) / CONFIG.IV_ALERT_THRESHOLD_PCT, 0.0, 1.0)

    return {
        "theta_on_track": theta_on_track,
        "strike_distance": strike_distance,
        "iv_stability": iv_stability,
    }


def health_score(axes: dict) -> float:
    """Weighted average, floored by any critical axis (the weakest-link rule)."""
    base = (CONFIG.W_THETA * axes["theta_on_track"]
            + CONFIG.W_STRIKE * axes["strike_distance"]
            + CONFIG.W_IV * axes["iv_stability"])
    critical = [v for v in axes.values() if v < CONFIG.CRIT]
    return round(min([base] + critical), 4)
