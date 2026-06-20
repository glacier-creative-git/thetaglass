# Robinhood data findings (live spike, 2026-06-19)

Captured from a real account holding one QQQ put credit spread. This is the ground
truth the state machine is designed against. Raw payloads were dumped via `tg dump`.

## The position used for this spike

QQQ put credit spread, **729/727**, exp 2026-07-17, qty 2, in the **margin** account
(`ACCT-1`, `agentic_allowed: false`). Reads worked fine on it via the agentic MCP
token — Thetaglass can monitor a non-agentic trading account.

- Short 729 put / long 727 put, $2 wide.
- Opened 2026-06-17 (30 DTE at open). Credit $150, max loss $250.
- Live: short-leg delta −0.39, ~70% chance of profit; QQQ spot ~$739.80 →
  short strike is only **1.46% OTM**.

## The four data calls (and how often each runs)

| Call | Returns | Cadence |
|------|---------|---------|
| `get_option_positions(account_number, nonzero=True)` | Open legs | **every tick** |
| `get_option_quotes(instrument_ids)` | IV + Greeks + mark, batched ≤20/call | **every tick** |
| `get_option_instruments(ids=...)` | strike, call/put, expiration | **once per new leg, cached** |
| `get_equity_quotes(symbols=[...])` | underlying spot (bid/ask) | every tick (cheap, batched) |

Param quirk: `get_option_instruments` takes `ids` as a **comma-separated string**;
`get_equity_quotes` takes `symbols` as a **JSON array**. Not symmetric.

## What each payload does and does NOT contain

### `get_option_positions` — a leg (NOT a strategy)
```jsonc
{
  "option_id": "2763cbc4-...",      // == instrument UUID; feeds quotes + instruments
  "chain_id": "a95fe906-...",       // shared across legs of the same underlying/expiry
  "chain_symbol": "QQQ",
  "type": "long",                    // long | short (position side, NOT call/put)
  "quantity": "2.0000",
  "average_price": "1738.0000",      // per-contract $ at entry; SIGN encodes debit/credit
  "expiration_date": "2026-07-17",
  "opened_at": "2026-06-17T15:06:36.191734Z"
}
```
- **No strike. No call/put.** Must be resolved via `get_option_instruments`.
- `average_price`: long leg `+1738` (paid $17.38/sh × 100), short leg `-1813`
  (received $18.13/sh × 100). So **credit_received = −Σ(average_price × quantity)** =
  −(1738·2 + (−1813)·2) = **$150**.

### `get_option_quotes` — live, per instrument
Has: `implied_volatility`, `delta`, `gamma`, `theta`, `vega`, `rho`, `mark_price`,
`bid_price`/`ask_price`, `chance_of_profit_short`, `break_even_price`,
`previous_close_price`, `updated_at`.
- **No strike, no call/put** here either.
- `updated_at` is the freshness signal (see market-hours note below).
- cost-to-close = Σ over legs of mark on the side needed to close; here
  (14.52 − 13.87)·100·2 = **$130** → P/L = 150 − 130 = **+$20** (~13% of max profit).

### `get_option_instruments(ids=...)` — static metadata (cache forever)
```jsonc
{ "id": "2763cbc4-...", "strike_price": "727.0000", "type": "put",
  "expiration_date": "2026-07-17" }
```
Strike/type/expiry never change for an id → resolve on first sighting, cache.
Paging a whole chain returns only ~100 contracts from strike 300 up, so do NOT
discover by chain — look up by `ids` directly.

### `get_equity_quotes(symbols=[...])` — underlying spot
Result item is `{ "quote": { "bid_price", "ask_price", ... }, "close": {...} }`.
Spot mid drives `distance_to_short_strike_pct`. QQQ ~739.80 here.

## Leg → strategy grouping (the identity rule)

The two legs of the spread share `chain_id`, `expiration_date`, `quantity`, and were
opened **22 ms apart** (`...191734Z` vs `...169468Z`) — one multi-leg fill.

Rule: legs sharing **(chain_id, expiration_date, quantity)** opened in a tight time
cluster = one strategy. **Stable position id = hash of the sorted set of option_ids.**
A roll/adjust changes the option_ids → correctly a brand-new position (preserving
history on the closed one).

Edge case (defer): two identical-width spreads on the same chain/expiry/qty —
disambiguate by pairing strikes. Rare for a normal book; handle in a later pass.

## Market calendar / freshness

Spike ran on 2026-06-19 (Juneteenth, market closed). Quotes were stamped from the
6/18 close (`previous_close_date: 2026-06-17`, quote `updated_at` on 6/18). The
Timekeeper must respect market hours and check `updated_at` so it neither recomputes
churn nor fires alerts on stale holiday data.

## Account shape

Two accounts: margin `ACCT-1` (`option_level_3`, holds the spread,
`agentic_allowed: false`) and cash `ACCT-2` ("Agentic", `agentic_allowed: true`,
no options). Iterate all accounts; the position to watch is in the non-agentic one.
