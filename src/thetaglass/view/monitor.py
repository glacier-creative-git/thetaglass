"""The interactive monitor (Layer E2) — `tg monitor`.

A Textual dashboard in three stacked cells: the selected position's P/L chart and
underlying chart each get a tall cell of their own (no shared Y axis), and a shorter,
scrollable list of dense position cards sits at the bottom. ↑/↓ moves the highlight and
both charts re-render for the newly selected position.

Textual earns its keep here precisely because Rich can't capture arrow keys. Each chart
is plotille's rgb-braille string wrapped via Text.from_ansi.
"""
from __future__ import annotations

from rich.text import Text
from textual.app import App, ComposeResult
from textual.widgets import Footer, Header, ListItem, ListView, Static

from thetaglass.view.cards import render_position_card
from thetaglass.view.chart import render_pnl_chart, render_underlying_chart

# entry = (position_dict, history_rows)
Entry = tuple[dict, list[dict]]


class PositionItem(ListItem):
    def __init__(self, entry: Entry):
        super().__init__()
        self.entry = entry

    def compose(self) -> ComposeResult:
        yield Static(render_position_card(self.entry[0]))


class MonitorApp(App):
    CSS = """
    #pnl   { height: 2fr; border: round $accent; padding: 0 1; }
    #under { height: 2fr; border: round $accent; padding: 0 1; }
    #plist { height: 1fr; min-height: 6; border: round $accent; }
    PositionItem { padding: 0 1; height: auto; }
    ListView > PositionItem.--highlight { background: $boost; }
    """
    BINDINGS = [("q", "quit", "Quit"), ("escape", "quit", "Quit")]

    def __init__(self, entries: list[Entry]):
        super().__init__()
        self.entries = entries
        self.current_idx = 0
        self.current_chart_text = ""   # pnl + underlying strings (for testability)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Static(id="pnl")
        yield Static(id="under")
        yield ListView(*[PositionItem(e) for e in self.entries], id="plist")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "Thetaglass — theta-decay monitor"
        self.query_one("#pnl", Static).border_title = "Position P/L"
        self.query_one("#under", Static).border_title = "Underlying"
        self.query_one("#plist", ListView).border_title = "Positions  (↑/↓ select · q quit)"
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
        pos, hist = self.entries[idx]
        pnl_w = self.query_one("#pnl", Static)
        und_w = self.query_one("#under", Static)
        pnl = render_pnl_chart(pos, hist, width=max(60, pnl_w.size.width - 2),
                               height=max(10, pnl_w.size.height - 1))
        und = render_underlying_chart(pos, hist, width=max(60, und_w.size.width - 2),
                                      height=max(10, und_w.size.height - 1))
        self.current_chart_text = pnl + und
        pnl_w.update(_nowrap(pnl))
        und_w.update(_nowrap(und))


def _nowrap(ansi: str) -> Text:
    """Wrap a plotille string for a widget WITHOUT letting Rich re-wrap long braille
    rows (which would scramble the y-axis). Overflow is cropped instead."""
    t = Text.from_ansi(ansi)
    t.no_wrap = True
    t.overflow = "crop"
    return t


def run_monitor(entries: list[Entry]) -> None:
    MonitorApp(entries).run()
