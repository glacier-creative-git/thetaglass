"""The interactive monitor (Layer E2) — `tg monitor`.

A Textual dashboard: a 2×2 grid of charts over a scrollable list of dense position cards.
↑/↓ moves the highlight and the charts re-render for the newly selected position.

  ┌ Position P/L ┬ Underlying ┐
  ├ IV vs entry  ┼ Health     ┤   ← health = the 3 axes (P/L, price, IV) scored + blended
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
from thetaglass.view.chart import (render_health_chart, render_iv_chart,
                                    render_pnl_chart, render_underlying_chart)
from thetaglass.view.overview import _label

# How a closed position's terminal_outcome reads on the receipt banner.
_OUTCOME = {
    "expired_max_profit": ("expired · max profit", "green"),
    "closed_early": ("closed early", "yellow"),
}

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
    #pnl, #under, #ivrv, #health {
        width: 1fr; height: 100%; border: round $accent; padding: 0 1;
    }
    #plist { height: 1fr; min-height: 8; border: round $accent; scrollbar-size: 1 1; }
    PositionItem { padding: 0 1; height: auto; }
    ListView > PositionItem.--highlight { background: $boost; }
    #receipt { height: 1; padding: 0 1; background: $boost; color: $text; }
    """
    BINDINGS = [("q", "quit", "Quit"), ("escape", "quit", "Quit")]

    def __init__(self, entries: list[Entry], receipt: bool = False):
        super().__init__()
        self.entries = entries
        self.current_idx = 0
        self.current_chart_text = ""   # pnl + underlying + ivrv strings (for testability)
        # receipt mode: a frozen, read-only view of CLOSED positions (`tg history`). Adds a
        # per-position outcome banner; the cells render the as-of-close snapshot.
        self.receipt = receipt

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        if self.receipt:
            yield Static(id="receipt")
        with Vertical(id="charts"):
            with Horizontal(classes="chartrow"):
                yield Static(id="pnl")
                yield Static(id="under")
            with Horizontal(classes="chartrow"):
                yield Static(id="ivrv")
                yield Static(id="health")
        yield ListView(*[PositionItem(e) for e in self.entries], id="plist")
        yield Footer()

    def on_mount(self) -> None:
        self.title = ("Thetaglass — position history" if self.receipt
                      else "Thetaglass — theta-decay monitor")
        self.query_one("#pnl", Static).border_title = "Position P/L"
        self.query_one("#under", Static).border_title = "Underlying"
        self.query_one("#ivrv", Static).border_title = "Implied Vol vs entry"
        self.query_one("#health", Static).border_title = "Health score"
        kind = "Closed" if self.receipt else "Positions"
        self.query_one("#plist", ListView).border_title = (
            f"{kind} ({len(self.entries)})  ↑/↓ select · q quit")
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
        if self.receipt:
            self.query_one("#receipt", Static).update(_receipt_banner(pos))
        pnl = self._draw("#pnl", render_pnl_chart, pos, hist)
        und = self._draw("#under", render_underlying_chart, pos, hist, closes)
        iv = self._draw("#ivrv", render_iv_chart, pos, hist)
        hl = self._draw("#health", render_health_chart, pos)
        self.current_chart_text = pnl + und + iv + hl

    def _draw(self, sel: str, fn, *args) -> str:
        w = self.query_one(sel, Static)
        s = fn(*args, width=max(48, w.size.width - 2), height=max(8, w.size.height - 1))
        w.update(_nowrap(s))
        return s


def _receipt_banner(pos: dict) -> Text:
    """The one-line CLOSED header for a frozen position: identity · outcome · final P/L · date."""
    label, outcome_color = _OUTCOME.get(pos.get("terminal_outcome"), ("closed", "white"))
    pl = pos.get("pl_dollars")
    closed = (pos.get("closed_at") or "")[:10]
    t = Text()
    t.append("CLOSED ", style="bold")
    t.append(f" {_label(pos)} ", style="bold")
    t.append(f"· {label} ", style=outcome_color)
    if pl is not None:
        t.append(f"· ${pl:+,.0f} ", style="green" if pl >= 0 else "red")
        # a gain reads against max PROFIT (how much we captured); a loss against max LOSS
        # (how deep) — dividing a loss by max profit gives a nonsense "-559% of max".
        if pl >= 0 and pos.get("max_profit"):
            t.append(f"({pl / pos['max_profit'] * 100:.0f}% of max profit) ", style="dim")
        elif pl < 0 and pos.get("max_loss"):
            t.append(f"({abs(pl) / pos['max_loss'] * 100:.0f}% of max loss) ", style="dim")
    if closed:
        t.append(f"· closed {closed}", style="dim")
    return t


def _nowrap(ansi: str) -> Text:
    """Wrap a plotille string for a widget WITHOUT letting Rich re-wrap long braille
    rows (which would scramble the y-axis). Overflow is cropped instead."""
    t = Text.from_ansi(ansi)
    t.no_wrap = True
    t.overflow = "crop"
    return t


def run_monitor(entries: list[Entry]) -> None:
    MonitorApp(entries).run()


def run_history(entries: list[Entry]) -> None:
    """Same depth as the monitor, but frozen receipts for CLOSED positions."""
    MonitorApp(entries, receipt=True).run()
