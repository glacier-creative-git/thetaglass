"""The broker interface Thetaglass depends on.

Thetaglass's state machine, health scoring, and alerting must NOT know which broker
they're reading from. Everything they need comes through this small read-only surface.
Robinhood is the only implementation in v1; IBKR (etc.) can be added later by
implementing the same contract without touching state/alert/server code.

Design rules for any implementation:
- READ ONLY. Thetaglass never places, modifies, or closes orders. A broker adapter
  exposes data accessors only — no order methods live on this interface.
- DETERMINISTIC. No LLM in the data path. These are plain data pulls.
- Return shapes are intentionally loose (`dict`/`list[dict]`) at this layer. The
  broker speaks its own native vocabulary here; normalizing those raw payloads into
  Thetaglass's canonical position model is the job of a higher layer, not the adapter.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class Broker(ABC):
    """Read-only market/account data source for the Timekeeper."""

    @abstractmethod
    def get_accounts(self) -> list[dict]:
        """All accounts visible to the authenticated session."""

    @abstractmethod
    def get_option_positions(self, account_number: str) -> list[dict]:
        """Open option *legs* for an account (non-zero quantity).

        Note: brokers typically report individual legs, not assembled strategies.
        Grouping legs into spreads is Thetaglass's responsibility, not the adapter's.
        """

    @abstractmethod
    def get_option_quotes(self, instrument_ids: list[str]) -> list[dict]:
        """Live quotes for option instruments: bid/ask plus IV and Greeks.

        This is the per-tick feed that drives decay/health recomputation. Implementations
        should handle any broker-side batch-size limits internally.
        """

    @abstractmethod
    def get_option_instruments(self, instrument_ids: list[str]) -> list[dict]:
        """Static contract metadata (strike, call/put, expiration) for instrument ids.

        Brokers tend to omit strike and option-type from position/quote payloads, yet
        Thetaglass needs them for max_loss, strategy classification, and distance-to-
        strike. This metadata never changes for a given id, so callers should resolve
        once on first sighting and cache it — NOT call this every tick.
        """

    @abstractmethod
    def get_equity_quotes(self, symbols: list[str]) -> list[dict]:
        """Underlying spot quotes. Needed for distance-to-short-strike; not in any
        options payload. Cheap and batchable across all distinct underlyings."""
