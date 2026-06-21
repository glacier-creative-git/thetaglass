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
from thetaglass.view.chart import (render_health_chart, render_iv_chart, render_pnl_chart,
                                   render_underlying_chart)
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


def test_pnl_chart_unrealized_line_is_blue():
    # the position is open, so it's UNrealized (mark-to-market) P/L — one blue line, with
    # the pre-watch bridge merged in (no separate green "realized" vs blue "backfill").
    BLUE = "\x1b[38;2;95;150;245m"
    pos, hist = make_mock_position(synced_after=4)
    assert min(h["dte_remaining"] for h in hist) < pos["dte_at_open"]  # history starts late
    s = render_pnl_chart(pos, hist, width=90, height=18)
    assert "unrealized P/L" in s and BLUE in s
    assert "realized P/L" not in s.replace("unrealized P/L", "")   # not labeled "realized"
    assert "backfill" not in s          # the word is retired; it's all just blue now


def test_underlying_chart_shows_edges():
    pos, hist = make_mock_position()
    s = render_underlying_chart(pos, hist, closes_from_history(hist), width=90, height=18)
    assert _has_braille(s)
    assert "UNDERLYING" in s and pos["underlying"] in s
    # the profit-% edge cone (BS run backwards), not flat strike lines
    assert "max-profit edge" in s and "90% (max) profit" in s and "break-even 0%" in s


def test_health_chart_is_snapshot_scoreboard():
    pos, _ = make_mock_position()
    s = render_health_chart(pos, width=66, height=14)
    assert f"{pos['health_score']:.2f}" in s                       # the compact number
    assert any(v in s for v in ("HEALTHY", "WATCH", "CRITICAL"))   # a verdict word
    # the three axes, each labeled with its blend weight (the weighting made explicit)
    assert "θ-track" in s and "strike" in s and "iv" in s
    assert "40%" in s and "20%" in s
    assert "◄" in s                              # the weakest axis is flagged
    assert "caps the score" in s                 # the floor-rule footnote
    assert any(0x2800 <= ord(c) <= 0x28FF for c in s)   # the braille θ/hourglass mark


def test_health_chart_falls_back_without_logo_when_cramped():
    # a short cell drops the logo and centers the scoreboard instead of cropping the mark
    pos, _ = make_mock_position()
    s = render_health_chart(pos, width=66, height=8)
    assert "θ-track" in s and f"{pos['health_score']:.2f}" in s
    assert not any(0x2800 <= ord(c) <= 0x28FF for c in s)   # no braille mark in cramped mode


def test_health_chart_flags_a_floored_axis():
    # a price breach: strike axis goes critical, so the whole score is floored to it
    pos, _ = make_mock_position()
    pos = {**pos, "health_score": 0.06,
           "health_axes": {"theta_on_track": 0.82, "strike_distance": 0.06,
                           "iv_stability": 0.71}}
    s = render_health_chart(pos, width=66, height=14)
    assert "CRITICAL" in s and "floored by strike" in s
    # red (C_BAD) is used for the breached axis; green (C_HEALTHY) for the healthy ones
    assert "\x1b[38;2;220;100;100m" in s and "\x1b[38;2;95;205;130m" in s


def test_health_chart_handles_missing_axes():
    pos, _ = make_mock_position()
    s = render_health_chart({**pos, "health_score": None, "health_axes": None})
    assert "unavailable" in s


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


def test_sand_animates_without_resetting_on_scroll():
    entries = make_mock_book(2)

    async def scenario():
        app = MonitorApp(entries)
        async with app.run_test(size=(150, 44)) as pilot:   # roomy enough to show the mark
            await pilot.pause()
            assert app._anim_frame == 0
            frame0 = app._render_health()

            app._tick_sand()                              # one animation tick
            await pilot.pause()
            assert app._anim_frame == 1
            frame1 = app._render_health()
            assert frame0 != frame1                       # the sand actually moved

            # scrolling to another position must NOT reset the running animation
            await pilot.press("down")
            await pilot.pause()
            assert app.current_idx == 1
            assert app._anim_frame == 1                   # frame counter untouched by selection

    asyncio.run(scenario())


def test_health_chart_sand_level_changes_render():
    pos, _ = make_mock_position()
    full_bottom = render_health_chart(pos, width=66, height=14, fill_top=0.0, fill_bottom=1.0)
    full_top = render_health_chart(pos, width=66, height=14, fill_top=1.0, fill_bottom=0.0)
    assert full_bottom != full_top                        # the sand level is reflected in the mark


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
