"""Mock data, the drill-down chart/card, and the interactive monitor.

The headline test drives the Textual app non-interactively (pilot): press ↓ and assert
the selection — and the rendered chart — actually change. So the REPL is tested despite
being a TUI.
"""
import asyncio
from io import StringIO

from rich.console import Console

from thetaglass.mock import MOCK_PREFIX, make_mock_book, make_mock_position
from thetaglass.view.cards import render_position_card
from thetaglass.view.chart import render_pnl_chart, render_underlying_chart
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
    syms = {p["underlying"] for p, _ in book}
    assert len(book) == 3 and len(syms) == 3


def test_pnl_chart_renders_cone():
    pos, hist = make_mock_position()
    s = render_pnl_chart(pos, hist, width=90, height=18)
    assert _has_braille(s)
    assert "P/L" in s
    assert "on-track" in s and "max loss" in s          # the cone legend


def test_underlying_chart_shows_edges():
    pos, hist = make_mock_position()
    s = render_underlying_chart(pos, hist, width=90, height=18)
    assert _has_braille(s)
    assert "UNDERLYING" in s and pos["underlying"] in s
    assert "profit edge" in s and "break-even" in s     # the outcome edges, not a flat strike


def test_card_contains_key_metrics():
    pos, _ = make_mock_position()
    out = _render(render_position_card(pos))
    assert pos["underlying"] in out
    assert "MOCK" in out and "health" in out and "DTE" in out


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
