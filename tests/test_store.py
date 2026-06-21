"""Store round-trip + the freeze guarantee.

The headline assertion: once a position is first seen, its baseline (especially
iv_at_entry) is frozen, so a later tick whose live quote has moved cannot drift the
IV-regime or health math. Plus snapshot append, positions_current overwrite, the
hybrid daily-close JSON, and the close-grace window.
"""
from datetime import date

import pytest

from thetaglass.state import compute
from thetaglass.state.models import Leg, Position
from thetaglass.store import Store


def _qqq_spread(short_iv: float = 0.250563) -> Position:
    long = Leg(option_id="L", side="long", option_type="put", strike=727, quantity=2,
               expiration="2026-07-17", average_price=1738.0,
               mark=13.865, iv=0.252798, delta=-0.374689, gamma=0.007408,
               theta=-0.323813, vega=0.773281)
    short = Leg(option_id="S", side="short", option_type="put", strike=729, quantity=2,
                expiration="2026-07-17", average_price=-1813.0,
                mark=14.520, iv=short_iv, delta=-0.389120, gamma=0.007605,
                theta=-0.323686, vega=0.782091)
    pos = Position(position_id="x", account_number="a", underlying="QQQ",
                   strategy_type="put_credit_spread", legs=[long, short])
    base = compute.freeze_baseline(pos.legs, "2026-06-17T15:06:36Z", 30)
    for k, v in base.items():
        setattr(pos, k, v)
    pos.iv_at_entry = short.iv          # first-sighting guess assemble would make
    compute.recompute_live(pos, spot=739.80, dte_remaining=28)
    return pos


@pytest.fixture()
def store(tmp_path):
    s = Store(tmp_path / "t.db")
    yield s
    s.close()


def test_first_sight_freezes_baseline(store):
    pos = _qqq_spread()
    store.record_tick([pos], tick_at="2026-06-18T20:00:00Z")

    row = store.position_row(pos.position_id)
    assert row["state"] == "open"
    assert row["first_seen_at"] == "2026-06-18T20:00:00Z"
    assert row["credit_received"] == 150.0
    assert row["max_loss"] == 250.0
    assert round(row["iv_at_entry"], 6) == 0.250563


def test_iv_at_entry_stays_frozen_when_quote_moves(store):
    # tick 1: entry IV recorded as 0.2506
    store.record_tick([_qqq_spread(short_iv=0.250563)], tick_at="2026-06-18T20:00:00Z")

    # tick 2: IV has since spiked to 0.30. assemble would naively set iv_at_entry=0.30.
    pos2 = _qqq_spread(short_iv=0.30)
    assert pos2.iv_at_entry == 0.30                      # the wrong guess, pre-store
    store.record_tick([pos2], tick_at="2026-06-18T20:05:00Z")

    # the store re-applied the frozen baseline and re-derived:
    assert round(pos2.iv_at_entry, 6) == 0.250563        # restored, not the live guess
    expected_delta = round((0.30 - 0.250563) / 0.250563, 4)
    assert pos2.iv_regime_delta_pct == expected_delta    # ~+19.7%, a real regime shift


def test_snapshots_append_and_current_overwrites(store):
    pid = _qqq_spread().position_id
    store.record_tick([_qqq_spread()], tick_at="2026-06-18T20:00:00Z")
    store.record_tick([_qqq_spread()], tick_at="2026-06-18T20:05:00Z")

    hist = store.history(pid)
    assert len(hist) == 2                                 # append-only
    assert [h["tick_at"] for h in hist] == sorted(h["tick_at"] for h in hist)

    current = store.current_positions()
    assert len(current) == 1                              # one row, overwritten
    assert current[0]["position_id"] == pid


def test_daily_close_carries_full_snapshot_json(store):
    pid = _qqq_spread().position_id
    store.record_tick([_qqq_spread()], tick_at="2026-06-18T20:00:00Z")          # intraday
    store.record_tick([_qqq_spread()], tick_at="2026-06-18T21:00:00Z",
                      is_daily_close=True)                                       # close

    intraday, close = store.history(pid)
    assert intraday["snapshot_json"] is None             # slim
    assert close["snapshot_json"] is not None            # full state-of-record
    assert close["is_daily_close"] == 1


def test_instrument_cache_round_trips(store):
    store.cache_instruments([{
        "id": "S", "chain_id": "c1", "chain_symbol": "QQQ", "type": "put",
        "strike_price": "729.0", "expiration_date": "2026-07-17",
    }])
    got = store.get_instruments(["S", "missing"])
    assert "missing" not in got
    assert got["S"]["strike_price"] == 729.0             # broker payload shape, typed
    assert got["S"]["type"] == "put"


def test_last_tick_at_tracks_latest(store):
    assert store.last_tick_at() is None                  # nothing synced yet
    store.record_tick([_qqq_spread()], tick_at="2026-06-18T20:00:00Z")
    store.record_tick([_qqq_spread()], tick_at="2026-06-18T20:05:00Z")
    assert store.last_tick_at() == "2026-06-18T20:05:00Z"


def test_equity_bars_round_trip(store):
    bars = [
        {"begins_at": "2026-06-01T00:00:00Z", "open_price": "100", "high_price": "102",
         "low_price": "99", "close_price": "101.5", "volume": 1000},
        {"begins_at": "2026-06-02T00:00:00Z", "open_price": "101.5", "high_price": "103",
         "low_price": "101", "close_price": "102.0", "volume": 1200},
    ]
    assert store.upsert_equity_bars("QQQ", bars) == 2
    closes = store.equity_closes("QQQ")
    assert closes == [("2026-06-01", 101.5), ("2026-06-02", 102.0)]
    assert store.latest_bar_date("QQQ") == "2026-06-02"
    # idempotent re-upsert (no duplicate rows)
    store.upsert_equity_bars("QQQ", bars)
    assert len(store.equity_closes("QQQ")) == 2


def test_close_grace_window(store):
    pos = _qqq_spread()
    pid = pos.position_id
    store.record_tick([pos], tick_at="t0")               # seen

    store.record_tick([], tick_at="t1")                  # 1st miss -> grace, still open
    assert store.position_row(pid)["state"] == "open"
    assert store.position_row(pid)["miss_count"] == 1

    store.record_tick([], tick_at="t2")                  # 2nd miss -> closed
    row = store.position_row(pid)
    assert row["state"] == "closed"
    assert row["closed_at"] == "t2"
    assert row["terminal_outcome"] in ("closed_early", "expired_max_profit")
    assert store.current_positions() == []               # dropped from the live view


def test_close_freezes_a_full_receipt(store):
    # closing snapshots the last full Position so `tg history` can show it as-of close
    pos = _qqq_spread()
    pid = pos.position_id
    store.record_tick([pos], tick_at="t0")               # last live state captured here
    store.record_tick([], tick_at="t1")                  # miss 1
    store.record_tick([], tick_at="t2")                  # miss 2 -> closed + frozen

    assert store.position_row(pid)["final_snapshot_json"] is not None

    closed = store.closed_positions()
    assert len(closed) == 1
    rec = closed[0]
    assert rec["position_id"] == pid and rec["underlying"] == "QQQ"
    # the receipt is the FULL Position (legs/axes), not just the slim snapshot columns
    assert "legs" in rec and "health_axes" in rec
    # lifecycle fields are merged on for the receipt header
    assert rec["state"] == "closed" and rec["closed_at"] == "t2"
    assert rec["terminal_outcome"] in ("closed_early", "expired_max_profit")


def test_closed_positions_empty_while_open(store):
    store.record_tick([_qqq_spread()], tick_at="t0")
    assert store.closed_positions() == []                # nothing closed yet
