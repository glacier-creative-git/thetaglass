"""The canonical position model (Layer A of docs/STATE_MACHINE.md).

This is the broker-agnostic shape everything downstream reads. Nothing here knows the
data came from Robinhood. Field provenance (FROZEN / CACHED / LIVE / DERIVED) is noted
in comments because it dictates what gets computed once vs. every tick.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class Leg:
    """One option contract in a strategy. Static facts + the live quote merged."""
    option_id: str                # CACHED key
    side: str                     # LIVE: 'long' | 'short' (position side, not call/put)
    option_type: str              # CACHED: 'call' | 'put'
    strike: float                 # CACHED
    quantity: float               # LIVE
    expiration: str               # CACHED
    average_price: float          # FROZEN: per-contract $ at entry; sign = debit/credit
    # live quote fields (None until quoted)
    mark: float | None = None
    iv: float | None = None
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None


@dataclass
class Position:
    """A grouped strategy with its frozen baseline and per-tick derived metrics."""
    # identity
    position_id: str              # DERIVED: hash of sorted leg ids
    account_number: str           # LIVE
    underlying: str               # LIVE
    strategy_type: str            # DERIVED
    legs: list[Leg] = field(default_factory=list)

    # frozen at open
    opened_at: str | None = None
    dte_at_open: int | None = None
    credit_received: float | None = None
    max_profit: float | None = None
    max_loss: float | None = None
    iv_at_entry: float | None = None

    # live
    dte_remaining: int | None = None
    underlying_price: float | None = None
    iv_now: float | None = None
    last_synced_at: str | None = None

    # derived
    current_value: float | None = None
    pl_dollars: float | None = None
    pl_pct_of_max_profit: float | None = None
    expected_pl_pct: float | None = None
    net_delta: float | None = None
    net_gamma: float | None = None
    net_theta: float | None = None
    net_vega: float | None = None
    distance_to_short_strike_pct: float | None = None
    iv_regime_delta_pct: float | None = None
    health_score: float | None = None
    # health axes, surfaced for the drill-down view and transparency
    health_axes: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def short_leg(self) -> Leg | None:
        return next((l for l in self.legs if l.side == "short"), None)
