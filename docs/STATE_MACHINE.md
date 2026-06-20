# Thetaglass State Machine — Design

> Design pass, no implementation. Every formula is worked out against the **real**
> QQQ 729/727 put credit spread from the live spike (see [FINDINGS.md](FINDINGS.md)),
> so we can see the numbers it produces before writing code.

## 0. The mental model (read this first)

You **sold** a credit spread. That means someone paid you money up front (the
*credit*), and your job is to give them back as little of it as possible before the
options expire. If the options expire worthless, you keep the whole credit — that's
your **max profit**. If the trade goes against you, you can lose up to the width of
the spread minus the credit — that's your **max loss**.

Two forces are always working at the same time:

1. **Theta (time decay) — your friend.** Options lose value as expiration approaches,
   and you profit when the options you're short lose value. "Theta" is just the dollars
   of value that melt away per day. Like sand falling through an hourglass — that's the
   name.
2. **Price movement — your risk.** If the underlying (QQQ) falls toward your **short
   strike**, the trade gets dangerous fast, decay or no decay.

Thetaglass's entire job is to answer one question on its own clock: **"Is this trade
melting the way I expected when I sold it, or has something gone wrong?"** To answer
that we need (a) a clean snapshot of the trade, (b) a stable way to recognize the same
trade over time, (c) somewhere to store the history, and (d) an honest definition of
"the way I expected." Those are the four layers below.

---

## 1. Layer A — The canonical `Position` model

The broker hands us raw, messy, per-leg data. We normalize it into ONE clean object
that everything else (health, alerts, CLI, MCP) reads. Nothing downstream is allowed
to know it came from Robinhood — that's what lets us add IBKR later.

The key idea is **provenance**: every field is tagged by *where it comes from and how
often it changes*. This tag is not decoration — it literally tells the code what to
compute once vs. what to recompute every 5 minutes.

| Provenance | Meaning | Cost |
|------------|---------|------|
| `FROZEN`   | Captured once when we first see the position; never changes | free after first sighting |
| `CACHED`   | Static contract facts (strike, put/call); resolved once, stored forever | one lookup per new leg |
| `LIVE`     | Pulled fresh from the broker every tick | one quote call per tick |
| `DERIVED`  | Computed by us each tick from the other three | pure math, no network |

### The model, filled in with your real spread

```jsonc
{
  // --- identity ---
  "position_id":        "a1b2c3…",            // DERIVED: hash of sorted leg ids (Layer B)
  "account_number":     "ACCT-1",           // LIVE
  "underlying":         "QQQ",                 // LIVE (chain_symbol)
  "strategy_type":      "put_credit_spread",   // DERIVED: classified from the legs

  "legs": [                                    // CACHED metadata + LIVE side
    { "option_id": "2763cbc4…", "side": "long",  "type": "put", "strike": 727, "qty": 2 },
    { "option_id": "0303b16b…", "side": "short", "type": "put", "strike": 729, "qty": 2 }
  ],

  // --- frozen at open (the baseline we measure against) ---
  "opened_at":          "2026-06-17T15:06:36Z",// FROZEN (earliest leg's opened_at)
  "dte_at_open":        30,                     // FROZEN: days from open to expiration
  "credit_received":    150.00,                 // FROZEN: −Σ(average_price × qty)
  "max_profit":         150.00,                 // FROZEN: = credit_received
  "max_loss":           250.00,                 // FROZEN: width($400) − credit($150)
  "iv_at_entry":        0.2506,                 // FROZEN*: IV at FIRST SIGHTING (see caveat)

  // --- live, every tick ---
  "dte_remaining":      28,                     // LIVE-ish: from today's date
  "underlying_price":   739.80,                 // LIVE: get_equity_quotes
  "iv_now":             0.2506,                 // LIVE: short-leg implied_volatility
  "greeks": { "delta": -0.39, "gamma": 0.0076, "theta": -0.32, "vega": 0.78 }, // LIVE per-leg
  "last_synced_at":     "2026-06-18T20:14:59Z", // LIVE: quote updated_at (freshness!)

  // --- derived, every tick ---
  "current_value":      131.00,   // DERIVED: cost to close now = (short_mark − long_mark)×100×qty
  "pl_dollars":         19.00,    // DERIVED: credit_received − current_value
  "pl_pct_of_max_profit": 0.127,  // DERIVED: pl_dollars / max_profit  (12.7% captured)
  "expected_pl_pct":    0.034,    // DERIVED: the baseline curve (Layer D) — "where you SHOULD be"
  "net_theta":          -0.03,    // DERIVED: Σ(side_sign × leg_theta × qty × 100)  (≈ 0 — see note)
  "distance_to_short_strike_pct": 0.0146,  // DERIVED: (spot − short_strike)/spot = 1.46%
  "iv_regime_delta_pct": 0.000,   // DERIVED: (iv_now − iv_at_entry)/iv_at_entry
  "health_score":       0.79      // DERIVED: the 0–1 score (Layer D)
}
```

### Three things this worked example teaches us

**1. The sign of `average_price` carries the credit/debit.** Long leg `+1738` (you
paid $17.38/share × 100), short leg `−1813` (you received $18.13/share × 100). So
`credit_received = −Σ(average_price × qty) = −(1738·2 + (−1813)·2) = $150`. No guessing.

**2. `iv_at_entry` has an honest caveat.** If Thetaglass starts watching a position
that was *already open*, the earliest IV we can record is the IV *when we first saw it*,
not the true entry IV. We tag it `FROZEN*` and store `first_seen_at` so the CLI can say
"IV baseline since first seen" rather than lying. If we're running when the position
opens, it's the real thing.

**3. Net theta can be ~0 — and that's a crucial design signal.** Your two strikes (727
and 729) are close together and near the money, so the short put's decay (good for you)
almost exactly cancels the long put's decay (bad for you): `+0.324 − 0.324 ≈ 0`. **This
is why we do NOT measure progress using instantaneous theta** — for a tight spread it's
nearly zero and tells you nothing. We measure **actual P/L vs. an expected P/L curve**
instead (Layer D). The real data proves the naive approach would break.

---

## 2. Layer B — Identity, grouping, and lifecycle

The broker gives us individual **legs**, not **strategies**. We have to (1) glue legs
into strategies, (2) give each strategy a stable name that survives across ticks, and
(3) track each strategy's life from open to close.

### Step-by-step grouping algorithm

```
INPUT: raw legs from get_option_positions() across all accounts

1. RESOLVE each leg's strike + type:
     look up option_id in the `instruments` cache;
     if missing, call get_option_instruments(ids=…) and store it (resolve-once).
   → each leg now = {option_id, account, chain_id, symbol, side, qty,
                     avg_price, expiration, strike, type, opened_at}

2. GROUP into candidate strategies by key:
     (account_number, chain_id, expiration_date, abs(qty))
   Then within a group, CLUSTER legs whose opened_at is within ~2 seconds
   of each other — legs of one spread are filled together (yours were 22 ms apart).

3. CLASSIFY strategy_type from the leg-set shape:
     1 short put                         → naked_short_put
     short put @higher + long put @lower  → put_credit_spread     ← yours
     short call@lower + long call@higher  → call_credit_spread
     long put@higher + short put@lower    → put_debit_spread
     4 legs, 2 puts + 2 calls             → iron_condor
     anything else                        → custom_multi_leg

4. ASSIGN stable id:
     position_id = sha1( "|".join(sorted(option_ids)) )
```

**Why hash the leg ids?** Because the legs ARE the trade's fingerprint. As long as the
same two contracts are open, it's the same position and the id is identical every tick.
The moment you **roll** (close these and open new strikes/expiry), the option_ids
change → a new id → correctly treated as a brand-new position, while the old one closes
with its history intact. Simple and robust.

**Known edge case (deferred to v1.1):** two *identical* spreads — same account, chain,
expiration, and quantity. Step 2 would lump them together. Fix later by pairing legs
into the minimum number of valid spreads by strike. Rare for a normal book; noted, not
solved now.

### The lifecycle finite-state machine

```
                first seen in feed
   (nothing) ───────────────────────► OPEN ──────────────► CLOSED
                                        │  ▲                  │
                          every tick:   │  │ still in feed    │ gone from feed
                          update LIVE +  └──┘                 │ for N ticks
                          append snapshot                     ▼
                                                       infer terminal_outcome
```

- **→ OPEN**: new position_id. Compute and **freeze** the baseline (credit, max_loss,
  dte_at_open, iv_at_entry). Insert into `positions`.
- **OPEN (stays)**: each tick, recompute LIVE + DERIVED fields, overwrite
  `positions_current`, and append one row to `snapshots` (this is the history that
  later draws the decay curve).
- **→ CLOSED**: the id was open but is no longer in the broker feed. To avoid closing on
  a single API hiccup, require **2 consecutive missing ticks** (a grace window). Then set
  `state=closed`, `closed_at=now`, and infer the outcome:
    - last snapshot `pl_pct ≈ 1.0`           → `expired_max_profit`
    - last-seen leg had `pending_assignment_quantity > 0` → `assigned`
    - last-seen leg had `pending_expiration_quantity > 0` → `expired`
    - otherwise                               → `closed_early`
  (Those `pending_*` fields are real in the position payload — we saw them at 0.0 — and
  flip non-zero right before a position resolves, which is our outcome signal.)

---

## 3. Layer C — The SQLite schema

One file: `var/thetaglass.db`, opened in **WAL mode** (Write-Ahead Logging). WAL lets
the Timekeeper write while the CLI and MCP server read at the same time without
blocking — which is exactly our two-process setup. Writes are tiny and infrequent, so
the occasional lock is a non-issue.

**Who writes what** (this is the discipline that keeps it sane):
- **Timekeeper** writes `instruments`, `positions`, `snapshots`, `positions_current`,
  and *inserts* new `alerts`.
- **MCP/HTTP server** writes ONLY `alerts` state transitions (acknowledge / resolve).
- **CLI** reads only.

```sql
-- Static contract metadata. Resolved once per option_id, then reused forever.
CREATE TABLE instruments (
    option_id    TEXT PRIMARY KEY,
    chain_id     TEXT,
    chain_symbol TEXT,
    option_type  TEXT,      -- 'call' | 'put'
    strike       REAL,
    expiration   TEXT,      -- ISO date
    resolved_at  TEXT
);

-- One row per strategy ever seen. Holds the FROZEN baseline + lifecycle state.
CREATE TABLE positions (
    position_id      TEXT PRIMARY KEY,   -- sha1 of sorted leg ids
    account_number   TEXT,
    underlying       TEXT,
    strategy_type    TEXT,
    legs_json        TEXT,               -- [{option_id, side, type, strike, qty}]
    state            TEXT,               -- 'open' | 'closed'
    opened_at        TEXT,
    first_seen_at    TEXT,
    dte_at_open      INTEGER,
    credit_received  REAL,
    max_profit       REAL,
    max_loss         REAL,
    iv_at_entry      REAL,
    closed_at        TEXT,               -- NULL while open
    terminal_outcome TEXT,               -- NULL while open
    miss_count       INTEGER DEFAULT 0,  -- consecutive ticks missing from feed (grace window)
    last_synced_at   TEXT
);

-- Append-only history. One row per OPEN position per tick. This IS the decay curve.
CREATE TABLE snapshots (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id         TEXT,
    tick_at             TEXT,
    dte_remaining       INTEGER,
    underlying_price    REAL,
    current_value       REAL,
    pl_dollars          REAL,
    pl_pct_of_max_profit REAL,
    expected_pl_pct     REAL,
    health_score        REAL,
    net_delta REAL, net_gamma REAL, net_theta REAL, net_vega REAL,
    iv_now              REAL,
    iv_regime_delta_pct REAL,
    distance_to_short_strike_pct REAL
);
CREATE INDEX idx_snap_pos_time ON snapshots(position_id, tick_at);

-- Materialized "latest" view: one row per OPEN position, overwritten each tick.
-- Lets `tg status` read instantly without scanning snapshots.
CREATE TABLE positions_current (
    position_id   TEXT PRIMARY KEY,
    snapshot_json TEXT,    -- the full canonical Position object (Layer A)
    updated_at    TEXT
);

-- The alert store. seq is the monotonic cursor that `peek` compares against.
CREATE TABLE alerts (
    seq             INTEGER PRIMARY KEY AUTOINCREMENT,  -- the cursor
    alert_id        TEXT UNIQUE,
    position_id     TEXT,
    event           TEXT,      -- 'theta_breach' | 'strike_breach' | 'iv_spike' | …
    severity        TEXT,      -- 'warning' | 'critical'
    metric          TEXT,
    value           REAL,
    threshold       REAL,
    summary         TEXT,
    suggested_action TEXT,
    state           TEXT,      -- 'open' | 'acknowledged' | 'resolved'
    created_at      TEXT,
    acknowledged_at TEXT,
    resolved_at     TEXT,
    resolution_note TEXT
);
CREATE INDEX idx_alerts_state ON alerts(state);
```

**The cursor.** `peek(since)` just runs `SELECT max(seq) > since AND state='open'`. The
`seq` is a plain auto-incrementing integer — cheap for a shell `curl` cron to compare,
exactly what Clock 2 needs.

**Retention.** Snapshots are tiny: ~78 ticks/market-day × ~30-day life ≈ 2,300 rows per
position, a few hundred KB. v1 keeps everything. Future knob: downsample snapshots of
*closed* positions to daily. Not worth building yet.

---

## 4. Layer D — The two judgment calls

Layers A–C are settled by the live data. These last two encode *opinion* about your
trading, so I'm presenting the options with your real numbers and a recommendation —
but these are yours to set.

### D1. The expected-decay baseline ("where SHOULD I be?")

This is the heart of the whole product. We need a curve that says, at any DTE, what
fraction of max profit a healthy trade should have captured by now. Three candidates,
each computed for your spread at **28 of 30 DTE** (actual captured = **12.7%**):

| Model | Formula | Says you should be at | Verdict on your trade |
|-------|---------|----------------------|----------------------|
| **Linear** | `elapsed / dte_at_open` = 2/30 | **6.7%** | ahead |
| **Theta-integration** | integrate current net theta forward | **~0%** (net theta ≈ 0!) | **breaks** |
| **√time (recommended)** | `1 − √(dte_remaining / dte_at_open)` | **3.4%** | ahead |

- **Linear is too naive.** It assumes decay is a straight line. It isn't — time decay
  *accelerates* as expiration nears.
- **Theta-integration looks rigorous but the real data kills it.** Your net theta is ≈ 0
  right now, so it would predict almost no profit ever. A single-snapshot theta is a
  terrible predictor for a spread.
- **√time wins for v1.** Options' time value shrinks roughly with the square root of
  time remaining — a real, well-known property. It needs only frozen facts (credit,
  dte_at_open) and today's DTE, so it's robust and cheap. Its curve is gently
  back-loaded (slow early, fast near expiry), which matches how credit spreads actually
  pay out:

  ```
  DTE remaining:  30    22    15     7     2     0
  should be at:    0%   14%   29%   52%   74%  100%
  ```

  **Recommendation: ship √time.** The exponent (0.5) becomes a tuning knob later if it
  feels too generous early. `theta_on_track = clamp(actual_pct / expected_pct, 0, 1.5)`,
  then capped at 1.0 for the health axis. For you: `0.127 / 0.034 = 3.7 → 1.0` (you're
  comfortably ahead of schedule).

### D2. The health score ("is anything wrong?")

A single 0–1 number. Built from three axes, each 0 (bad) to 1 (good):

```
theta_on_track  = clamp(pl_pct_of_max_profit / expected_pl_pct, 0, 1.5)  capped 1.0
strike_distance = clamp(distance_to_short_strike_pct / breach_threshold_pct, 0, 1.0)
iv_stability    = clamp(1 − |iv_regime_delta_pct| / iv_alert_threshold_pct, 0, 1.0)
```

**The trap to avoid: a plain weighted average hides a disaster.** If we just averaged
the three, a position about to blow through your short strike (strike_distance → 0)
could still score ~0.6 because the other two axes look fine. For a *risk* monitor that's
backwards — one critical axis should dominate.

**Recommended fix — a "weakest-link floor":**
```
base   = 0.4·theta_on_track + 0.4·strike_distance + 0.2·iv_stability
health = min( base, every axis that is below CRIT )      # CRIT = 0.34
       = base, if no axis is critical
```
So normally health is the weighted average, but the moment *any* axis goes critical,
health can't rise above that axis. The weakest link wins when there's danger.

**Your spread, with default knobs** (weights 0.4/0.4/0.2, breach 3%, iv-alert 15%):

| Axis | Value | Why |
|------|-------|-----|
| theta_on_track | **1.00** | 12.7% captured vs 3.4% expected → way ahead, capped at 1.0 |
| strike_distance | **0.49** | 1.46% buffer ÷ 3% threshold → only half your "safe" cushion |
| iv_stability | **1.00** | just first-seen, no IV change yet |

`base = 0.4·1.0 + 0.4·0.49 + 0.2·1.0 = 0.79`. No axis below 0.34, so **health = 0.79.**

That's an honest read: *solidly profitable and ahead of schedule, but it's a near-the-
money spread, so the thin price cushion is the thing to watch.* Exactly the nuance a
plain "you're up $19" misses.

**Why the floor matters — the stress test:** if QQQ fell to 729 (your short strike),
`strike_distance → 0`. A plain average would still say `0.4·1.0 + 0.2·1.0 = 0.6`
("looks okay!"). The floor says `min(0.6, 0.0) = 0.0` ("this is in trouble"). That gap
is the whole reason for the floor.

---

## 5. Open decisions — the knobs (my recommended defaults)

These are the values I'd ship; flag any you want different and they change one config
block, nothing structural.

| Knob | Recommended default | What it controls |
|------|--------------------|------------------|
| decay baseline | **√time**, exponent 0.5 | the "where should I be" curve |
| health weights | theta **0.4**, strike **0.4**, iv **0.2** | what dominates the score |
| critical floor `CRIT` | **0.34** | when one bad axis overrides the average |
| `breach_threshold_pct` | **3%** | price cushion that counts as "safe" |
| `iv_alert_threshold_pct` | **15%** | IV jump from entry that's alarming |
| close grace window | **2 missing ticks** | avoid closing on an API hiccup |
| snapshot retention | **keep everything** | history depth |

Once these feel right, Layer A–C is ready to implement as written, and D is just
transcribing the two formulas above.
