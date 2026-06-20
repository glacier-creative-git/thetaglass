"""The persistence layer (Layer C). One writer (Timekeeper), many readers (CLI, MCP).

The Store owns the canonical history. Its central method is `record_tick`, which runs
the lifecycle for one sync:
  * first sight of a position  -> FREEZE the baseline, insert it
  * every later sight          -> re-apply the frozen baseline, re-derive, update
  * a position gone from feed  -> increment a grace counter, close after N misses

The freeze-or-reconcile step is the point of the whole layer: `assemble` can only guess
`iv_at_entry` from the current quote, so on repeat ticks we overwrite that guess with the
value we froze on day one and recompute, guaranteeing the IV-regime and health math never
drift just because the live quote moved.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from thetaglass.settings import CONFIG, DB_PATH, VAR_DIR
from thetaglass.state import compute
from thetaglass.state.models import Position

# Frozen-at-open columns, copied back onto a live Position on every repeat tick.
_FROZEN_FIELDS = (
    "opened_at", "dte_at_open", "credit_received",
    "max_profit", "max_loss", "iv_at_entry",
)

# Slim snapshot columns written every tick (the trend series agents read).
_SNAP_METRICS = (
    "dte_remaining", "underlying_price", "current_value", "pl_dollars",
    "pl_pct_of_max_profit", "expected_pl_pct", "health_score",
    "net_delta", "net_gamma", "net_theta", "net_vega",
    "iv_now", "iv_regime_delta_pct", "distance_to_short_strike_pct",
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, db_path: Path | str = DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        from thetaglass.store.schema import SCHEMA_SQL
        # WAL lets the Timekeeper write while CLI/MCP read without blocking.
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA_SQL)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- instrument cache (resolve-once) -----------------------------------

    def get_instruments(self, option_ids: list[str]) -> dict[str, dict]:
        """Return cached metadata keyed by option_id, in broker payload shape so
        callers (assemble) can treat cache hits and fresh lookups identically."""
        if not option_ids:
            return {}
        qs = ",".join("?" * len(option_ids))
        rows = self.conn.execute(
            f"SELECT * FROM instruments WHERE option_id IN ({qs})", option_ids
        ).fetchall()
        return {
            r["option_id"]: {
                "id": r["option_id"],
                "chain_id": r["chain_id"],
                "chain_symbol": r["chain_symbol"],
                "type": r["option_type"],
                "strike_price": r["strike"],
                "expiration_date": r["expiration"],
            }
            for r in rows
        }

    def cache_instruments(self, instruments: list[dict]) -> None:
        """Persist freshly resolved instrument metadata (broker payload shape)."""
        now = _utcnow()
        self.conn.executemany(
            """INSERT OR REPLACE INTO instruments
               (option_id, chain_id, chain_symbol, option_type, strike, expiration, resolved_at)
               VALUES (?,?,?,?,?,?,?)""",
            [
                (m["id"], m.get("chain_id"), m.get("chain_symbol"),
                 m.get("type"), _f(m.get("strike_price")),
                 m.get("expiration_date"), now)
                for m in instruments
            ],
        )

    # --- the per-tick lifecycle write --------------------------------------

    def record_tick(self, positions: list[Position], tick_at: str | None = None,
                    is_daily_close: bool = False) -> list[str]:
        """Persist one sync. Returns the position_ids seen this tick."""
        tick_at = tick_at or _utcnow()
        seen: list[str] = []
        for pos in positions:
            self._freeze_or_reconcile(pos, tick_at)
            self._append_snapshot(pos, tick_at, is_daily_close)
            self._upsert_current(pos, tick_at)
            seen.append(pos.position_id)
        self._mark_absent(seen, tick_at)
        return seen

    def _freeze_or_reconcile(self, pos: Position, tick_at: str) -> None:
        row = self.conn.execute(
            "SELECT * FROM positions WHERE position_id=?", (pos.position_id,)
        ).fetchone()

        if row is None:
            # First sight: this tick's values ARE the frozen baseline.
            self.conn.execute(
                """INSERT INTO positions
                   (position_id, account_number, underlying, strategy_type, legs_json,
                    state, opened_at, first_seen_at, dte_at_open, credit_received,
                    max_profit, max_loss, iv_at_entry, miss_count, last_synced_at)
                   VALUES (?,?,?,?,?, 'open', ?,?,?,?, ?,?,?, 0, ?)""",
                (pos.position_id, pos.account_number, pos.underlying, pos.strategy_type,
                 json.dumps(_legs_json(pos)), pos.opened_at, tick_at, pos.dte_at_open,
                 pos.credit_received, pos.max_profit, pos.max_loss, pos.iv_at_entry,
                 tick_at),
            )
            return

        # Repeat sight: the DB is the source of truth for frozen fields. Re-apply
        # them onto the live Position and re-derive so nothing drifts off a moved quote.
        for fld in _FROZEN_FIELDS:
            setattr(pos, fld, row[fld])
        compute.recompute_live(pos, pos.underlying_price, pos.dte_remaining)
        self.conn.execute(
            "UPDATE positions SET state='open', miss_count=0, last_synced_at=? WHERE position_id=?",
            (tick_at, pos.position_id),
        )

    def _append_snapshot(self, pos: Position, tick_at: str, is_daily_close: bool) -> None:
        cols = ", ".join(_SNAP_METRICS)
        ph = ", ".join("?" * len(_SNAP_METRICS))
        vals = [getattr(pos, m) for m in _SNAP_METRICS]
        snap_json = json.dumps(pos.to_dict(), default=str) if is_daily_close else None
        self.conn.execute(
            f"""INSERT INTO snapshots
                (position_id, tick_at, is_daily_close, {cols}, snapshot_json)
                VALUES (?, ?, ?, {ph}, ?)""",
            [pos.position_id, tick_at, 1 if is_daily_close else 0, *vals, snap_json],
        )

    def _upsert_current(self, pos: Position, tick_at: str) -> None:
        self.conn.execute(
            """INSERT INTO positions_current (position_id, snapshot_json, updated_at)
               VALUES (?,?,?)
               ON CONFLICT(position_id) DO UPDATE SET
                   snapshot_json=excluded.snapshot_json, updated_at=excluded.updated_at""",
            (pos.position_id, json.dumps(pos.to_dict(), default=str), tick_at),
        )

    def _mark_absent(self, seen: list[str], tick_at: str) -> None:
        """Grace window: open positions missing from the feed accrue misses, and
        flip to closed only after CLOSE_GRACE_TICKS consecutive absences."""
        placeholders = ",".join("?" * len(seen)) if seen else "''"
        missing = self.conn.execute(
            f"""SELECT position_id, miss_count FROM positions
                WHERE state='open' AND position_id NOT IN ({placeholders})""",
            seen,
        ).fetchall()
        for r in missing:
            n = (r["miss_count"] or 0) + 1
            if n >= CONFIG.CLOSE_GRACE_TICKS:
                self.conn.execute(
                    """UPDATE positions SET state='closed', closed_at=?,
                       terminal_outcome=?, miss_count=? WHERE position_id=?""",
                    (tick_at, self._infer_outcome(r["position_id"]), n, r["position_id"]),
                )
                self.conn.execute(
                    "DELETE FROM positions_current WHERE position_id=?", (r["position_id"],)
                )
            else:
                self.conn.execute(
                    "UPDATE positions SET miss_count=? WHERE position_id=?",
                    (n, r["position_id"]),
                )

    def _infer_outcome(self, position_id: str) -> str:
        """Best-effort terminal outcome from the last snapshot. The pending_assignment/
        expiration signals (STATE_MACHINE.md §2) need leg-level fields we don't persist
        yet, so v1 distinguishes max-profit expiry from an early close by final P/L."""
        row = self.conn.execute(
            """SELECT pl_pct_of_max_profit FROM snapshots
               WHERE position_id=? ORDER BY tick_at DESC LIMIT 1""",
            (position_id,),
        ).fetchone()
        if row and (row["pl_pct_of_max_profit"] or 0) >= 0.99:
            return "expired_max_profit"
        return "closed_early"

    # --- reads (CLI / MCP) -------------------------------------------------

    def current_positions(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT snapshot_json FROM positions_current ORDER BY position_id"
        ).fetchall()
        return [json.loads(r["snapshot_json"]) for r in rows]

    def position_row(self, position_id: str) -> dict | None:
        r = self.conn.execute(
            "SELECT * FROM positions WHERE position_id=?", (position_id,)
        ).fetchone()
        return dict(r) if r else None

    def history(self, position_id: str, daily_only: bool = False,
                since: str | None = None) -> list[dict]:
        sql = "SELECT * FROM snapshots WHERE position_id=?"
        params: list = [position_id]
        if daily_only:
            sql += " AND is_daily_close=1"
        if since:
            sql += " AND tick_at >= ?"
            params.append(since)
        sql += " ORDER BY tick_at"
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]


def _legs_json(pos: Position) -> list[dict]:
    return [
        {"option_id": l.option_id, "side": l.side, "type": l.option_type,
         "strike": l.strike, "qty": l.quantity, "expiration": l.expiration,
         "average_price": l.average_price}
        for l in pos.legs
    ]


def _f(x) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# ensure var/ exists for the default path even before first Store()
VAR_DIR.mkdir(parents=True, exist_ok=True)
