"""The expected-decay baseline (Layer D1).

Answers: "at this DTE, what fraction of max profit SHOULD a healthy trade have captured
by now?" We use the √time model — option time-value shrinks roughly with the square root
of time remaining, a real and well-known property. It needs only frozen facts, so it's
robust and cheap (no live theta, which we proved is ~0 and useless for a tight spread).

    expected_captured_fraction(dte) = 1 − (dte_remaining / dte_at_open) ** exponent

exponent 0.5 = √time; <1 makes the curve back-loaded (slow early, fast near expiry),
matching how credit spreads actually pay out.
"""
from __future__ import annotations

from thetaglass.settings import CONFIG


def expected_pl_pct(dte_remaining: int, dte_at_open: int, exponent: float | None = None) -> float:
    """Fraction of max profit a healthy trade should have captured by `dte_remaining`."""
    if dte_at_open is None or dte_at_open <= 0:
        return 0.0
    exp = CONFIG.DECAY_EXPONENT if exponent is None else exponent
    remaining = max(0, dte_remaining) / dte_at_open
    remaining = min(1.0, remaining)
    return 1.0 - (remaining ** exp)
