"""The interactive monitor (Layer E2) — `tg monitor`.

A Textual dashboard: the chart for the highlighted position fills the top; the bottom is
an arrow-navigable list of dense position cards. Move the highlight with ↑/↓ and the
chart re-renders for the newly selected position — the REPL the design called for, with
no "type a number" step.

Textual earns its keep here precisely because Rich can't capture arrow keys. The chart is
plotille's rgb-braille string wrapped via Text.from_ansi so it renders inside the widget.
"""
from __future__ import annotations

from rich.text import Text
from textual.app import App, ComposeResult
from textual.widgets import Footer, Header, ListItem, ListView, Static

from thetaglass.view.cards import render_position_card
from thetaglass.view.chart import render_position_chart

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
    #chart { height: 3fr; border: round $accent; padding: 0 1; }
    #plist { height: 2fr; border: round $accent; }
    PositionItem { padding: 0 1; height: auto; }
    ListView > PositionItem.--highlight { background: $boost; }
    """
    BINDINGS = [("q", "quit", "Quit"), ("escape", "quit", "Quit")]

    def __init__(self, entries: list[Entry]):
        super().__init__()
        self.entries = entries
        self.current_idx = 0
        self.current_chart_text = ""   # the plotille string of the shown chart (testable)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Static(id="chart")
        yield ListView(*[PositionItem(e) for e in self.entries], id="plist")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "Thetaglass — theta-decay monitor"
        plist = self.query_one("#plist", ListView)
        plist.focus()
        if self.entries:
            plist.index = 0
            self._render_chart(0)

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        idx = self.query_one("#plist", ListView).index
        if idx is not None:
            self._render_chart(idx)

    def on_resize(self, event) -> None:
        self._render_chart(self.current_idx)

    def _render_chart(self, idx: int) -> None:
        if not self.entries:
            return
        self.current_idx = idx
        pos, hist = self.entries[idx]
        chart = self.query_one("#chart", Static)
        w = max(60, chart.size.width - 2)
        h = max(16, chart.size.height - 1)
        self.current_chart_text = render_position_chart(pos, hist, width=w, height=h)
        chart.update(Text.from_ansi(self.current_chart_text))


def run_monitor(entries: list[Entry]) -> None:
    MonitorApp(entries).run()
