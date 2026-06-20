"""Mock data, the drill-down chart/card, and the interactive monitor.

The headline test drives the Textual app non-interactively (pilot): press ↓ and assert
the selection — and the rendered chart — actually change. So the REPL is tested despite
being a TUI.
"""
import asyncio
from io import StringIO

from rich.console import Console

from thetaglass.mock import MOCK_PREFIX, closes_from_history, make_mock_book, make_mock_position
from thetaglass.state.volatility import realized_vol, rv_series
from thetaglass.view.cards import render_position_card
from thetaglass.view.chart import render_iv_chart, render_pnl_chart, render_underlying_chart
from thetaglass.view.monitor import MonitorApp

BRAILLE = range(0x2800, 0x28FF + 1)


def _has_braille(s: str) -> bool:
    return any(ord(c) in BRAILLE for c in s)


def _render(renderable, width=100) -> str:
    c = Console(width=width, file=StringIO(), force_terminal=False)
    c.print(renderable)
    return c.file.getvalue()


def test_mock_position_shape():
    pos, hist = make_mock_position()
    assert pos["is_mock"] is True
    assert pos["position_id"].startswith(MOCK_PREFIX)
    assert pos["max_profit"] and pos["max_loss"]
    assert len(hist) > 5
    # history rows carry the columns views/agents read
    assert {"tick_at", "underlying_price", "pl_dollars"} <= set(hist[0])


def test_mock_book_is_distinct():
    book = make_mock_book(3)
    syms = {pos["underlying"] for pos, _hist, _closes in book}
    assert len(book) == 3 and len(syms) == 3
    # every entry carries a (date, close) series for RV
    assert all(closes and len(closes[0]) == 2 for _pos, _hist, closes in book)


def test_pnl_chart_renders_cone():
    pos, hist = make_mock_position()
    s = render_pnl_chart(pos, hist, width=90, height=18)
    assert _has_braille(s)
    assert "P/L" in s
    assert "on-track" in s and "max loss" in s          # the cone legend


def test_pnl_chart_backfills_gap_in_blue():
    # started watching 4 days after open → a blue backfill bridge is drawn + keyed
    pos, hist = make_mock_position(synced_after=4)
    assert min(h["dte_remaining"] for h in hist) < pos["dte_at_open"]  # history starts late
    s = render_pnl_chart(pos, hist, width=90, height=18)
    assert "backfill" in s
    # contiguous history (watched from open) → no backfill line
    pos2, hist2 = make_mock_position(synced_after=0)
    assert "backfill" not in render_pnl_chart(pos2, hist2, width=90, height=18)


def test_underlying_chart_shows_edges():
    pos, hist = make_mock_position()
    s = render_underlying_chart(pos, hist, closes_from_history(hist), width=90, height=18)
    assert _has_braille(s)
    assert "UNDERLYING" in s and pos["underlying"] in s
    # the profit-% edge cone (BS run backwards), not flat strike lines
    assert "max-profit edge" in s and "90% (max) profit" in s and "break-even 0%" in s


def test_card_contains_key_metrics():
    pos, _ = make_mock_position()
    out = _render(render_position_card(pos))
    assert pos["underlying"] in out
    assert "MOCK" in out and "health" in out and "DTE" in out


def test_realized_vol_is_annualized():
    # a steady 1%/day zig-zag → nonzero annualized vol; flat series → ~0
    import math
    closes = [100 * (1.01 if i % 2 else 1 / 1.01) ** 1 for i in range(40)]
    rv = realized_vol(closes, window=20)
    assert rv is not None and rv > 0
    assert realized_vol([100.0] * 40, window=20) == 0.0  # no movement → 0 vol
    assert realized_vol([100.0, 101.0], window=20) is None  # too little data


def test_iv_chart_renders_vs_entry():
    pos, hist = make_mock_position(synced_after=4)
    s = render_iv_chart(pos, hist, width=90, height=16)
    assert "IMPLIED VOL vs entry" in s
    assert "IV < entry (good)" in s and "IV > entry (bad)" in s   # the green/red key
    assert "IV@entry" in s
    assert _has_braille(s)


def test_rv_series_grows_with_history():
    pos, hist = make_mock_position()
    series = rv_series(closes_from_history(hist), window=10)
    assert series and all(v > 0 for _d, v in series)


def test_mock_book_scales_to_distinct_positions():
    book = make_mock_book(6)
    syms = {pos["underlying"] for pos, _h, _c in book}
    assert len(book) == 6 and len(syms) == 6      # six distinct demo positions


def test_nav_scrolls_through_full_book():
    entries = make_mock_book(6)

    async def scenario():
        app = MonitorApp(entries)
        async with app.run_test(size=(150, 44)) as pilot:
            await pilot.pause()
            for _ in range(5):                      # step from 0 → 5
                await pilot.press("down")
                await pilot.pause()
            assert app.current_idx == 5             # reached the last, list scrolled to it

    asyncio.run(scenario())


def test_arrow_nav_switches_chart():
    entries = make_mock_book(2)

    async def scenario():
        app = MonitorApp(entries)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.current_idx == 0
            first = app.current_chart_text
            await pilot.press("down")
            await pilot.pause()
            assert app.current_idx == 1
            second = app.current_chart_text
            assert first != second                        # the chart actually changed

    asyncio.run(scenario())
