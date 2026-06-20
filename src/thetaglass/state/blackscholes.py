"""Black-Scholes pricing + implied-volatility inversion.

Used for one job: recover the IV we *sold at* from the real entry fill price and the
underlying's price on the open day (both real) — the only honest way to anchor IV before
Thetaglass started watching. European BS, no dividends; for short-dated near-the-money US
equity options that's the standard approximation, and it's the same model the IV we're
handed is quoted under anyway.
"""
from __future__ import annotations

import math

R = 0.04          # risk-free rate assumption (short-dated → small effect on IV)
_MAX_VOL = 5.0    # 500% vol ceiling for the solver


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_price(option_type: str, S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Black-Scholes price of a European call/put."""
    if T <= 0 or sigma <= 0:
        return max(0.0, (S - K) if option_type == "call" else (K - S))   # intrinsic
    srt = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / srt
    d2 = d1 - srt
    if option_type == "call":
        return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)
    return K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)


def implied_vol(option_type: str, price: float, S: float, K: float, T: float,
                r: float = R, iters: int = 80) -> float | None:
    """Invert BS for sigma via bisection (price is monotincreasing in vol). None if the
    price is below intrinsic or beyond a 500%-vol ceiling (i.e. not invertible)."""
    if price <= 0 or T <= 0 or S <= 0 or K <= 0:
        return None
    intrinsic = max(0.0, (S - K) if option_type == "call" else (K - S))
    if price < intrinsic - 1e-6:
        return None
    if price > bs_price(option_type, S, K, T, r, _MAX_VOL):
        return None
    lo, hi = 1e-4, _MAX_VOL
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        if bs_price(option_type, S, K, T, r, mid) > price:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)


def implied_entry_iv(option_type: str, fill_per_share: float, S: float, K: float,
                     dte_days: float, r: float = R) -> float | None:
    """The IV implied by an entry fill price (per share), at the open-day underlying."""
    return implied_vol(option_type, fill_per_share, S, K, max(dte_days, 0) / 365.0, r)


def spread_cost_to_close(legs: list[dict], S: float, T: float, iv: float, r: float = R) -> float:
    """Dollar cost to close a vertical at underlying S / time-to-expiry T (years), holding
    IV flat: buy back shorts (pay), sell longs (receive)."""
    total = 0.0
    for l in legs:
        sign = 1.0 if l["side"] == "short" else -1.0
        total += sign * bs_price(l["option_type"], S, l["strike"], T, r, iv) * 100 * abs(l.get("quantity") or 1)
    return total


def underlying_for_profit(legs: list[dict], credit: float, max_profit: float, f: float,
                          T: float, iv: float, r: float = R, iters: int = 50) -> float | None:
    """The underlying price S at which the spread's P/L = f·max_profit, at time-to-expiry T.

    Black-Scholes run backwards: P/L(S) = credit − cost_to_close(S) is monotone in S for a
    vertical, so bisect. This is the 'what does the stock need to be for X% profit, this
    many days out' curve — converging toward the strikes as T → 0.
    """
    target = f * max_profit

    def pl(S: float) -> float:
        return credit - spread_cost_to_close(legs, S, T, iv, r)

    lo, hi = 1.0, max(l["strike"] for l in legs) * 3.0
    plo, phi = pl(lo), pl(hi)
    if not (min(plo, phi) - 1e-6 <= target <= max(plo, phi) + 1e-6):
        return None
    increasing = phi > plo
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        if (pl(mid) < target) == increasing:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def position_entry_iv(pos: dict, closes: list[tuple[str, float]]) -> float | None:
    """Reconstruct a position's entry IV from its short leg's fill and the underlying's
    close on the open day. `closes` is the (date, close) series. None if inputs missing."""
    short = next((l for l in pos.get("legs", []) if l.get("side") == "short"), None)
    if not short or not closes or not pos.get("dte_at_open"):
        return None
    open_d = (pos.get("opened_at") or "")[:10]
    s_open = next((c for d, c in reversed(closes) if d <= open_d), None)
    if not s_open:
        return None
    fill = abs(short.get("average_price") or 0.0) / 100.0   # per-contract $ → per share
    if fill <= 0:
        return None
    return implied_entry_iv(short["option_type"], fill, s_open, short["strike"],
                            pos["dte_at_open"])
