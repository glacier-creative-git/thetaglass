"""The interactive monitor (Layer E2) — `tg monitor`.

A Textual dashboard: a 2×2 grid of charts over a scrollable list of dense position cards.
↑/↓ moves the highlight and the charts re-render for the newly selected position.

  ┌ Position P/L ┬ Underlying ┐
  ├ IV vs RV     ┼ [EMPTY]    ┤   ← reserved cell for a future view
  └ positions (arrow-navigable list) ┘

Textual earns its keep here precisely because Rich can't capture arrow keys. Each chart
is plotille's rgb-braille string wrapped via Text.from_ansi.
"""
from __future__ import annotations

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, ListItem, ListView, Static

from thetaglass.view.cards import render_position_card
from thetaglass.view.chart import (render_iv_chart, render_pnl_chart,
                                    render_underlying_chart)

# entry = (position_dict, history_rows, underlying_closes)
Entry = tuple[dict, list[dict], list]


class PositionItem(ListItem):
    def __init__(self, entry: Entry):
        super().__init__()
        self.entry = entry

    def compose(self) -> ComposeResult:
        yield Static(render_position_card(self.entry[0]))


class MonitorApp(App):
    # Charts sit side-by-side: each gets the full height (≈2× the vertical braille
    # resolution of a stacked split), so the cone edges separate into their own cells.
    # TODO(adaptive): on narrow terminals (< ~110 cols) each cell gets cramped; could
    # switch to a stacked layout below a width breakpoint. Stubbed for now.
    # 2×2 chart grid. Each cell is half-width/half-height of the charts area.
    # TODO(adaptive): on narrow terminals the cells get cramped; could switch to a
    # stacked single column below a width breakpoint. Stubbed for now.
    CSS = """
    #charts   { height: 3fr; }
    .chartrow { height: 1fr; }
    #pnl, #under, #ivrv, #empty {
        width: 1fr; height: 100%; border: round $accent; padding: 0 1;
    }
    #empty { color: $text-muted; content-align: center middle; }
    #plist { height: 1fr; min-height: 8; border: round $accent; scrollbar-size: 1 1; }
    PositionItem { padding: 0 1; height: auto; }
    ListView > PositionItem.--highlight { background: $boost; }
    """
    BINDINGS = [("q", "quit", "Quit"), ("escape", "quit", "Quit")]

    def __init__(self, entries: list[Entry]):
        super().__init__()
        self.entries = entries
        self.current_idx = 0
        self.current_chart_text = ""   # pnl + underlying + ivrv strings (for testability)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical(id="charts"):
            with Horizontal(classes="chartrow"):
                yield Static(id="pnl")
                yield Static(id="under")
            with Horizontal(classes="chartrow"):
                yield Static(id="ivrv")
                yield Static("[EMPTY]", id="empty")
        yield ListView(*[PositionItem(e) for e in self.entries], id="plist")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "Thetaglass — theta-decay monitor"
        self.query_one("#pnl", Static).border_title = "Position P/L"
        self.query_one("#under", Static).border_title = "Underlying"
        self.query_one("#ivrv", Static).border_title = "Implied Vol vs entry"
        self.query_one("#empty", Static).border_title = "—"
        self.query_one("#plist", ListView).border_title = (
            f"Positions ({len(self.entries)})  ↑/↓ select · q quit")
        plist = self.query_one("#plist", ListView)
        plist.focus()
        if self.entries:
            plist.index = 0
            self._render_charts(0)

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        idx = self.query_one("#plist", ListView).index
        if idx is not None:
            self._render_charts(idx)

    def on_resize(self, event) -> None:
        self._render_charts(self.current_idx)

    def _render_charts(self, idx: int) -> None:
        if not self.entries:
            return
        self.current_idx = idx
        pos, hist, closes = self.entries[idx]
        pnl = self._draw("#pnl", render_pnl_chart, pos, hist)
        und = self._draw("#under", render_underlying_chart, pos, hist, closes)
        iv = self._draw("#ivrv", render_iv_chart, pos, hist)
        self.current_chart_text = pnl + und + iv

    def _draw(self, sel: str, fn, *args) -> str:
        w = self.query_one(sel, Static)
        s = fn(*args, width=max(48, w.size.width - 2), height=max(8, w.size.height - 1))
        w.update(_nowrap(s))
        return s


def _nowrap(ansi: str) -> Text:
    """Wrap a plotille string for a widget WITHOUT letting Rich re-wrap long braille
    rows (which would scramble the y-axis). Overflow is cropped instead."""
    t = Text.from_ansi(ansi)
    t.no_wrap = True
    t.overflow = "crop"
    return t


def run_monitor(entries: list[Entry]) -> None:
    MonitorApp(entries).run()
