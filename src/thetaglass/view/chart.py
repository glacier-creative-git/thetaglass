"""Per-position drill-down charts (Layer E2) — two INDEPENDENT plotille cells.

Underlying price and position P/L can't share a Y axis honestly, so we don't try: each
is its own self-scaled chart with full vertical room. Both share only the conceptual X
(days since open). plotille draws rgb braille and returns a string, so each slots into
its own Textual cell via Text.from_ansi.

  render_pnl_chart        — P/L in dollars (up = made money): realized path + forecast
                            cone (linear→max-profit, linear→max-loss, √time on-track).
  render_underlying_chart — underlying price + the outcome edges (short/long strike,
                            break-even) and a forward cone projecting today's price to
                            those edges at expiration.

Pure: each takes a Position dict + snapshot history, returns an ANSI string.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

import plotille

# rgb line colors (plotille color_mode="rgb")
C_UNDER = (90, 200, 250)     # underlying price — cyan
C_PROFIT = (95, 200, 120)    # profit edge / max profit — green
C_LOSS = (215, 95, 95)       # loss edge / max loss — red
C_BE = (230, 205, 95)        # break-even — amber
C_REAL = (120, 230, 150)     # realized P/L — bright green
C_SQRT = (235, 205, 95)      # √time on-track — amber
C_BEST = (95, 160, 110)      # linear → max profit — dim green
C_WORST = (200, 115, 115)    # linear → max loss — dim red
C_ZERO = (110, 110, 120)     # break-even / reference — grey
C_CONE = (140, 140, 155)     # forward projection cone — dim grey


def _parse(ts: str) -> datetime:
    s = ts.replace("Z", "+00:00")
    if len(s) == 10:
        s += "T00:00:00+00:00"
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _days_since(open_dt: datetime, ts: str) -> float:
    return (_parse(ts) - open_dt).total_seconds() / 86400.0


def _context(pos: dict, history: list[dict]):
    open_dt = _parse(pos["opened_at"])
    dte0 = pos.get("dte_at_open") or 1
    xs_real = [_days_since(open_dt, h["tick_at"]) for h in history]
    now_x = max(xs_real) if xs_real else (dte0 - (pos.get("dte_remaining") or 0))
    return open_dt, dte0, xs_real, now_x


def _new_fig(width: int, height: int, dte0: float, ylabel: str) -> plotille.Figure:
    fig = plotille.Figure()
    fig.width, fig.height = max(40, width), max(6, height)
    fig.color_mode = "rgb"
    fig.x_label, fig.y_label = "day", ylabel
    fig.set_x_limits(min_=0, max_=dte0)
    return fig


# --------------------------------------------------------------------------- P/L

def render_pnl_chart(pos: dict, history: list[dict], width: int = 90, height: int = 16) -> str:
    _, dte0, xs_real, now_x = _context(pos, history)
    max_profit = pos.get("max_profit") or 0.0
    max_loss = pos.get("max_loss") or 0.0

    fig = _new_fig(width, height, dte0, "P/L $")
    fig.set_y_limits(min_=-max_loss * 1.1 if max_loss else -10,
                     max_=max_profit * 1.15 if max_profit else 10)

    n = 48
    xs = [dte0 * i / n for i in range(n + 1)]
    fig.plot([0, dte0], [0, 0], lc=C_ZERO, label="break-even")
    fig.plot(xs, [max_profit * (x / dte0) for x in xs], lc=C_BEST, label="linear→max profit")
    fig.plot(xs, [-max_loss * (x / dte0) for x in xs], lc=C_WORST, label="linear→max loss")
    fig.plot(xs, [max_profit * (1 - math.sqrt(max(0.0, (dte0 - x) / dte0))) for x in xs],
             lc=C_SQRT, label="√time on-track")

    ys = [h["pl_dollars"] for h in history if h.get("pl_dollars") is not None]
    if ys:
        fig.plot(xs_real[:len(ys)], ys, lc=C_REAL, label="realized P/L")

    title = (f" P/L (up = made money)    max profit ${max_profit:g}   "
             f"max loss ${max_loss:g}   day {now_x:.0f}/{dte0}")
    return title + "\n" + fig.show(legend=True)


# -------------------------------------------------------------------- underlying

def render_underlying_chart(pos: dict, history: list[dict],
                            width: int = 90, height: int = 16) -> str:
    _, dte0, xs_real, now_x = _context(pos, history)
    legs = pos["legs"]
    short = next((l for l in legs if l["side"] == "short"), legs[0])
    long = next((l for l in legs if l["side"] == "long"), None)
    is_put = short["option_type"] == "put"

    prices = [h["underlying_price"] for h in history if h.get("underlying_price") is not None]
    spot = prices[-1] if prices else (pos.get("underlying_price") or short["strike"])

    profit_k = short["strike"]                       # stay on the safe side of this → keep credit
    loss_k = long["strike"] if long else None        # past this → full max loss
    credit_ps = (pos.get("credit_received") or 0.0) / (100 * abs(short["quantity"] or 1))
    break_even = profit_k - credit_ps if is_put else profit_k + credit_ps

    levels = [profit_k, spot] + ([loss_k] if loss_k else []) + [break_even]
    lo, hi = min(levels + prices), max(levels + prices)
    pad = (hi - lo) * 0.12 or 1.0

    fig = _new_fig(width, height, dte0, "$")
    fig.set_y_limits(min_=lo - pad, max_=hi + pad)

    # outcome edges — the prices that decide the trade
    fig.plot([0, dte0], [profit_k, profit_k], lc=C_PROFIT, label=f"profit edge {profit_k:g}")
    if loss_k:
        fig.plot([0, dte0], [loss_k, loss_k], lc=C_LOSS, label=f"max-loss edge {loss_k:g}")
    fig.plot([0, dte0], [break_even, break_even], lc=C_BE, label=f"break-even {break_even:.2f}")

    # forward cone: from today's price, project to each edge at expiration
    if now_x < dte0:
        fig.plot([now_x, dte0], [spot, profit_k], lc=C_CONE, label="cone → edges @ exp")
        if loss_k:
            fig.plot([now_x, dte0], [spot, loss_k], lc=C_CONE, label=" ")

    # realized underlying price (left of NOW)
    if prices:
        fig.plot(xs_real[:len(prices)], prices, lc=C_UNDER, label=f"{pos['underlying']} price")

    title = (f" UNDERLYING  {pos['underlying']}  spot ${spot:g}   "
             f"profit edge {profit_k:g}   break-even {break_even:.2f}")
    return title + "\n" + fig.show(legend=True)
