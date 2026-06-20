"""Leg → strategy grouping, classification, and stable identity (Layer B).

The broker reports individual legs; we glue them into strategies, name each one stably,
and classify its shape. The stable id is a hash of the sorted leg ids, so the same
contracts always map to the same position, and a roll (different contracts) is correctly
a brand-new position.
"""
from __future__ import annotations

import hashlib
from datetime import datetime

from thetaglass.state.models import Leg

CLUSTER_SECONDS = 5  # legs of one strategy are filled within this window (yours: 22 ms)


def stable_position_id(legs: list[Leg]) -> str:
    key = "|".join(sorted(l.option_id for l in legs))
    return hashlib.sha1(key.encode()).hexdigest()


def _parse(ts: str) -> float:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()


def group_legs(legs_with_meta: list[tuple[Leg, dict]]) -> list[list[Leg]]:
    """Group resolved legs into strategies.

    Input: (Leg, raw) pairs where raw carries account_number, chain_id, expiration,
    opened_at. Group by (account, chain, expiration, |qty|), then cluster by opened_at
    proximity so two separate same-day spreads on one chain don't merge.
    """
    buckets: dict[tuple, list[tuple[Leg, dict]]] = {}
    for leg, raw in legs_with_meta:
        key = (raw["account_number"], raw["chain_id"], leg.expiration, abs(leg.quantity))
        buckets.setdefault(key, []).append((leg, raw))

    groups: list[list[Leg]] = []
    for items in buckets.values():
        items.sort(key=lambda lr: _parse(lr[1]["opened_at"]))
        cluster: list[tuple[Leg, dict]] = []
        last_t: float | None = None
        for leg, raw in items:
            t = _parse(raw["opened_at"])
            if last_t is not None and t - last_t > CLUSTER_SECONDS:
                groups.append([l for l, _ in cluster])
                cluster = []
            cluster.append((leg, raw))
            last_t = t
        if cluster:
            groups.append([l for l, _ in cluster])
    return groups


def classify(legs: list[Leg]) -> str:
    """Name the strategy from its leg shape. v1 covers the common defined-risk forms."""
    puts = [l for l in legs if l.option_type == "put"]
    calls = [l for l in legs if l.option_type == "call"]

    if len(legs) == 1:
        l = legs[0]
        if l.side == "short":
            return "naked_short_put" if l.option_type == "put" else "naked_short_call"
        return "long_put" if l.option_type == "put" else "long_call"

    if len(legs) == 2 and len(puts) == 2:
        short = next((l for l in puts if l.side == "short"), None)
        long = next((l for l in puts if l.side == "long"), None)
        if short and long:
            # credit: sell the higher-strike put; debit: buy the higher-strike put.
            return "put_credit_spread" if short.strike > long.strike else "put_debit_spread"

    if len(legs) == 2 and len(calls) == 2:
        short = next((l for l in calls if l.side == "short"), None)
        long = next((l for l in calls if l.side == "long"), None)
        if short and long:
            return "call_credit_spread" if short.strike < long.strike else "call_debit_spread"

    if len(legs) == 4 and len(puts) == 2 and len(calls) == 2:
        return "iron_condor"

    return "custom_multi_leg"
