"""Assemble canonical Positions from a Broker (the read pipeline, no persistence yet).

Orchestrates the four broker calls from FINDINGS.md into Layer A objects:
  positions (legs)  →  resolve strikes (cached)  →  group  →  quotes + spot  →  compute.

This slice runs live and returns Positions; wiring it to the SQLite store + Timekeeper
(freezing iv_at_entry across ticks, appending snapshots) is the next step.
"""
from __future__ import annotations

from datetime import date, datetime

from thetaglass.broker.base import Broker
from thetaglass.state import compute, identity
from thetaglass.state.models import Leg, Position


def _f(x) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _dte(expiration: str, ref: date) -> int:
    exp = date.fromisoformat(expiration)
    return (exp - ref).days


def assemble_positions(broker: Broker, today: date | None = None) -> list[Position]:
    today = today or datetime.utcnow().date()

    # 1. raw legs across all accounts, tagged with their account number
    raw_legs: list[dict] = []
    for acct in broker.get_accounts():
        acct_no = acct.get("account_number")
        if not acct_no:
            continue
        for p in broker.get_option_positions(acct_no):
            p = {**p, "account_number": acct_no}
            raw_legs.append(p)
    if not raw_legs:
        return []

    # 2. resolve strike/type for every leg (one cached call; static metadata)
    option_ids = [p["option_id"] for p in raw_legs]
    meta = {i["id"]: i for i in broker.get_option_instruments(option_ids)}

    legs_with_meta: list[tuple[Leg, dict]] = []
    for p in raw_legs:
        m = meta.get(p["option_id"], {})
        leg = Leg(
            option_id=p["option_id"],
            side=p["type"],                              # 'long' | 'short'
            option_type=m.get("type", "?"),
            strike=_f(m.get("strike_price")) or 0.0,
            quantity=_f(p.get("quantity")) or 0.0,
            expiration=p.get("expiration_date") or m.get("expiration_date", ""),
            average_price=_f(p.get("average_price")) or 0.0,
        )
        legs_with_meta.append((leg, p))

    # 3. live quotes, keyed by instrument id, attached to legs
    quotes = {q.get("instrument_id"): q for q in broker.get_option_quotes(option_ids)}
    for leg, _ in legs_with_meta:
        q = quotes.get(leg.option_id, {})
        leg.mark = _f(q.get("mark_price"))
        leg.iv = _f(q.get("implied_volatility"))
        leg.delta = _f(q.get("delta"))
        leg.gamma = _f(q.get("gamma"))
        leg.theta = _f(q.get("theta"))
        leg.vega = _f(q.get("vega"))

    # 4. underlying spot per distinct symbol (mid of bid/ask)
    raw_by_id = {leg.option_id: raw for leg, raw in legs_with_meta}
    symbols = sorted({raw["chain_symbol"] for raw in raw_by_id.values()})
    spot = _spots(broker, symbols)

    # 5. group into strategies and build each Position
    positions: list[Position] = []
    for group in identity.group_legs(legs_with_meta):
        first_raw = raw_by_id[group[0].option_id]
        opened_at = min(raw_by_id[l.option_id]["opened_at"] for l in group)
        expiration = group[0].expiration
        underlying = first_raw["chain_symbol"]

        pos = Position(
            position_id=identity.stable_position_id(group),
            account_number=first_raw["account_number"],
            underlying=underlying,
            strategy_type=identity.classify(group),
            legs=group,
        )
        base = compute.freeze_baseline(group, opened_at, _dte(expiration, _odate(opened_at)))
        for k, v in base.items():
            setattr(pos, k, v)
        # first sighting: iv_at_entry = current short-leg IV (Timekeeper will persist this)
        pos.iv_at_entry = (pos.short_leg.iv if pos.short_leg else None)
        pos.last_synced_at = _latest_quote_ts(quotes, group)

        compute.recompute_live(pos, spot.get(underlying), _dte(expiration, today))
        positions.append(pos)

    return positions


def _spots(broker: Broker, symbols: list[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for r in broker.get_equity_quotes(symbols):
        q = r.get("quote", r)
        bid, ask = _f(q.get("bid_price")), _f(q.get("ask_price"))
        sym = q.get("symbol") or r.get("symbol")
        if bid and ask:
            out[sym] = round((bid + ask) / 2, 4)
    # get_equity_quotes may not echo the symbol; if single symbol, map it directly
    if len(symbols) == 1 and symbols[0] not in out and out:
        out[symbols[0]] = next(iter(out.values()))
    return out


def _odate(opened_at: str) -> date:
    return datetime.fromisoformat(opened_at.replace("Z", "+00:00")).date()


def _latest_quote_ts(quotes: dict, legs: list[Leg]) -> str | None:
    ts = [quotes.get(l.option_id, {}).get("updated_at") for l in legs]
    ts = [t for t in ts if t]
    return max(ts) if ts else None
