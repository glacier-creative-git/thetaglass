"""The per-position drill-down chart (Layer E2) — two stacked plotille charts.

plotille draws rgb braille and returns a plain string, so it slots straight into a
Textual widget (monitor) or a Rich panel. Two charts share one X axis (days since open):

  Chart 1 — UNDERLYING price over the life so far, with the short strike as a dashed
            danger line, so price drifting toward the strike is visible at a glance.
  Chart 2 — P/L in dollars, "up = we made money": the realized path (left of NOW) plus
            the forecast cone (right): linear→max-profit, linear→max-loss, and the
            √time on-track curve that accelerates toward max profit near expiry.

Pure: takes a Position dict + its snapshot history, returns an ANSI string. No store.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

import plotille

# rgb line colors (plotille color_mode="rgb")
C_UNDER = (90, 200, 250)     # underlying — cyan
C_STRIKE = (210, 90, 90)     # short strike danger line — red
C_REAL = (90, 220, 120)      # realized P/L — green
C_SQRT = (235, 205, 95)      # √time on-track — amber
C_BEST = (95, 160, 110)      # linear → max profit — dim green
C_WORST = (200, 115, 115)    # linear → max loss — dim red
C_ZERO = (110, 110, 120)     # break-even / reference — grey


def _parse(ts: str) -> datetime:
    s = ts.replace("Z", "+00:00")
    if len(s) == 10:
        s += "T00:00:00+00:00"
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _days_since(open_dt: datetime, ts: str) -> float:
    return (_parse(ts) - open_dt).total_seconds() / 86400.0


def render_position_chart(pos: dict, history: list[dict],
                          width: int = 90, height: int = 24) -> str:
    open_dt = _parse(pos["opened_at"])
    dte0 = pos.get("dte_at_open") or 1
    max_profit = pos.get("max_profit") or 0.0
    max_loss = pos.get("max_loss") or 0.0
    short = next((l for l in pos["legs"] if l["side"] == "short"), pos["legs"][0])

    xs_real = [_days_since(open_dt, h["tick_at"]) for h in history]
    now_x = max(xs_real) if xs_real else (dte0 - (pos.get("dte_remaining") or 0))

    h_each = max(7, (height - 2) // 2)
    plot_w = max(40, width - 14)            # leave room for plotille's y-axis labels

    under = _underlying_chart(pos, history, xs_real, short, dte0, now_x, plot_w, h_each)
    pnl = _pnl_chart(history, xs_real, dte0, now_x, max_profit, max_loss, plot_w, h_each)
    return under + "\n" + pnl


def _underlying_chart(pos, history, xs_real, short, dte0, now_x, w, h) -> str:
    fig = plotille.Figure()
    fig.width, fig.height = w, h
    fig.color_mode = "rgb"
    fig.x_label, fig.y_label = "day", "$"
    fig.set_x_limits(min_=0, max_=dte0)

    ys = [h_["underlying_price"] for h_ in history if h_.get("underlying_price") is not None]
    strike = short["strike"]
    if ys:
        lo, hi = min(ys + [strike]), max(ys + [strike])
        pad = (hi - lo) * 0.1 or 1.0
        fig.set_y_limits(min_=lo - pad, max_=hi + pad)
        fig.plot(xs_real[:len(ys)], ys, lc=C_UNDER, label=f"{pos['underlying']} price")
    # short strike: the line price must not cross
    fig.plot([0, dte0], [strike, strike], lc=C_STRIKE, label=f"short {strike:g}")
    title = (f" UNDERLYING  {pos['underlying']}  "
             f"spot ${pos.get('underlying_price', '?')}  short strike {strike:g}")
    return title + "\n" + fig.show(legend=True)


def _pnl_chart(history, xs_real, dte0, now_x, max_profit, max_loss, w, h) -> str:
    fig = plotille.Figure()
    fig.width, fig.height = w, h
    fig.color_mode = "rgb"
    fig.x_label, fig.y_label = "day", "P/L $"
    fig.set_x_limits(min_=0, max_=dte0)
    fig.set_y_limits(min_=-max_loss * 1.1 if max_loss else -10,
                     max_=max_profit * 1.15 if max_profit else 10)

    # forecast curves across the whole life (sampled)
    n = 48
    xs = [dte0 * i / n for i in range(n + 1)]
    best = [max_profit * (x / dte0) for x in xs]                       # linear → max profit
    worst = [-max_loss * (x / dte0) for x in xs]                       # linear → max loss
    sqrt = [max_profit * (1 - math.sqrt(max(0.0, (dte0 - x) / dte0))) for x in xs]  # √time
    fig.plot([0, dte0], [0, 0], lc=C_ZERO, label="break-even")        # the 0 line
    fig.plot(xs, best, lc=C_BEST, label="linear→max profit")
    fig.plot(xs, worst, lc=C_WORST, label="linear→max loss")
    fig.plot(xs, sqrt, lc=C_SQRT, label="√time on-track")

    # realized P/L path (left of NOW) — the position's actual value history
    ys = [h_["pl_dollars"] for h_ in history if h_.get("pl_dollars") is not None]
    if ys:
        fig.plot(xs_real[:len(ys)], ys, lc=C_REAL, label="realized P/L")

    title = (f" P/L (up = made money)   max profit ${max_profit:g}   "
             f"max loss ${max_loss:g}   NOW = day {now_x:.0f}/{dte0}")
    return title + "\n" + fig.show(legend=True)
