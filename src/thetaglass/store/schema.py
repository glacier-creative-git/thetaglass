"""The SQLite schema (Layer C of docs/STATE_MACHINE.md), verbatim.

One file, WAL mode. Writer discipline:
  * Timekeeper writes instruments, positions, snapshots, positions_current, and
    INSERTs alerts.
  * The MCP/HTTP server writes only alert state transitions.
  * The CLI reads only.
Kept as a standalone DDL string so it's trivially testable and diffable.
"""
from __future__ import annotations

SCHEMA_SQL = """
-- Static contract metadata. Resolved once per option_id, then reused forever.
CREATE TABLE IF NOT EXISTS instruments (
    option_id    TEXT PRIMARY KEY,
    chain_id     TEXT,
    chain_symbol TEXT,
    option_type  TEXT,      -- 'call' | 'put'
    strike       REAL,
    expiration   TEXT,      -- ISO date
    resolved_at  TEXT
);

-- One row per strategy ever seen. Holds the FROZEN baseline + lifecycle state.
CREATE TABLE IF NOT EXISTS positions (
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

-- Append-only history. One row per OPEN position per tick. This IS the decay curve
-- AND the trend history agents read. HYBRID storage:
--   * every tick  -> the slim scalar columns (enough to draw any trend line)
--   * daily close -> is_daily_close=1 AND snapshot_json = the full Position object
CREATE TABLE IF NOT EXISTS snapshots (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id         TEXT,
    tick_at             TEXT,
    is_daily_close      INTEGER DEFAULT 0,
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
    distance_to_short_strike_pct REAL,
    snapshot_json       TEXT                 -- NULL intraday; full Position on daily close
);
CREATE INDEX IF NOT EXISTS idx_snap_pos_time  ON snapshots(position_id, tick_at);
CREATE INDEX IF NOT EXISTS idx_snap_pos_daily ON snapshots(position_id, is_daily_close);

-- Materialized "latest" view: one row per OPEN position, overwritten each tick.
CREATE TABLE IF NOT EXISTS positions_current (
    position_id   TEXT PRIMARY KEY,
    snapshot_json TEXT,    -- the full canonical Position object (Layer A)
    updated_at    TEXT
);

-- The alert store. seq is the monotonic cursor that `peek` compares against.
CREATE TABLE IF NOT EXISTS alerts (
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
CREATE INDEX IF NOT EXISTS idx_alerts_state ON alerts(state);
"""
