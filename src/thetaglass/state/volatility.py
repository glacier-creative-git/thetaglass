"""Realized volatility (RV) from underlying closes — the backward-looking counterpart to
the option's implied volatility (IV).

RV is the annualized standard deviation of daily log returns over a trailing window. The
IV vs RV gap is the variance risk premium: IV > RV means options are richly priced
(good for a premium seller); IV < RV means they're cheap relative to how the underlying
is actually moving (a risk flag for a credit-spread watchdog).
"""
from __future__ import annotations

import math
import statistics

TRADING_DAYS = 252
DEFAULT_WINDOW = 20


def realized_vol(closes: list[float], window: int = DEFAULT_WINDOW) -> float | None:
    """Annualized RV from the last `window` returns of `closes`. Falls back to whatever
    history is available (down to 2 returns) so short series still produce a value."""
    if len(closes) < 3:
        return None
    series = closes[-(window + 1):]
    rets = [math.log(series[i] / series[i - 1]) for i in range(1, len(series))
            if series[i - 1] > 0 and series[i] > 0]
    if len(rets) < 2:
        return None
    return statistics.stdev(rets) * math.sqrt(TRADING_DAYS)


def rv_series(dated_closes: list[tuple[str, float]],
              window: int = DEFAULT_WINDOW) -> list[tuple[str, float]]:
    """Rolling RV: one (date, annualized_rv) per day that has enough history behind it."""
    out: list[tuple[str, float]] = []
    closes = [c for _, c in dated_closes]
    for i in range(1, len(dated_closes)):
        v = realized_vol(closes[: i + 1], window)
        if v is not None:
            out.append((dated_closes[i][0], v))
    return out
