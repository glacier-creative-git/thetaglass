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

from datetime import datetime, timezone

import plotille

from thetaglass.state.baseline import expected_pl_pct
from thetaglass.state.volatility import DEFAULT_WINDOW, realized_vol, rv_series

# rgb line colors (plotille color_mode="rgb")
C_UNDER = (90, 200, 250)     # underlying price — cyan
C_PROFIT = (95, 200, 120)    # profit edge / max profit — green
C_LOSS = (215, 95, 95)       # loss edge / max loss — red
C_BE = (230, 205, 95)        # break-even — amber
C_REAL = (120, 230, 150)     # realized P/L — bright green
C_BACKFILL = (95, 150, 245)  # estimated pre-watch P/L (linear bridge) — blue
C_SQRT = (95, 165, 110)      # √time on-track (cone top) — dark green
C_WORST = (200, 115, 115)    # linear → max loss (cone bottom) — dim red
C_CONE = (140, 140, 155)     # forward projection cone — dim grey
C_IV = (205, 130, 235)       # implied volatility — magenta
C_RV = (235, 165, 80)        # realized volatility — orange
C_IVENTRY = (130, 110, 150)  # IV at entry reference — dim purple


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


# plotille frames the braille canvas with a y-label gutter (left, ~14) and a "> (x)"
# suffix (right, ~8). `width`/`height` are the CELL's content box; reserve the frame so
# lines never exceed the cell and wrap. We render our own compact colored key instead of
# plotille's multi-line legend, so the only vertical overhead is the axis frame + 2 lines.
_X_GUTTER = 24
_Y_GUTTER = 6


def _new_fig(width: int, height: int, dte0: float, ylabel: str) -> plotille.Figure:
    fig = plotille.Figure()
    fig.width = max(30, width - _X_GUTTER)
    fig.height = max(6, height - _Y_GUTTER)
    fig.color_mode = "rgb"
    # Suppress plotille's origin cross-hairs: when the y-range spans 0 it otherwise draws
    # a full-width grey axis line at $0 straight through the chart, which collides with the
    # colored cone lines where they cross break-even. Axis labels/frame are unaffected.
    fig.origin = False
    fig.x_label, fig.y_label = "day", ylabel
    fig.set_x_limits(min_=0, max_=dte0)
    fig.y_ticks_fkt = lambda v, _: f"{v:,.0f}"      # whole dollars / prices, no 10-digit noise
    fig.x_ticks_fkt = lambda v, _: f"{v:.0f}"       # whole days
    return fig


def _key(*items: tuple[str, tuple[int, int, int]]) -> str:
    """A compact one-line color key (ANSI rgb), replacing plotille's tall legend."""
    return "  ".join(f"\x1b[38;2;{r};{g};{b}m{lab}\x1b[0m" for lab, (r, g, b) in items)


# --------------------------------------------------------------------------- P/L

def render_pnl_chart(pos: dict, history: list[dict], width: int = 90, height: int = 16) -> str:
    _, dte0, xs_real, now_x = _context(pos, history)
    max_profit = pos.get("max_profit") or 0.0
    max_loss = pos.get("max_loss") or 0.0

    fig = _new_fig(width, height, dte0, "P/L $")
    fig.set_y_limits(min_=-max_loss * 1.1 if max_loss else -10,
                     max_=max_profit * 1.15 if max_profit else 10)
    # X stays plotted as days-since-open (0→dte0) but READS as days-to-expiration: the
    # leftmost tick is the DTE at open, the rightmost is 0. Data positions are unchanged.
    fig.x_label = "days to exp"
    fig.x_ticks_fkt = lambda v, _: f"{dte0 - v:.0f}"

    ys = [h["pl_dollars"] for h in history if h.get("pl_dollars") is not None]
    realized_now = ys[-1] if ys else (pos.get("pl_dollars") or 0.0)

    # The cone is a FORECAST: it starts at NOW (anchored to the current realized value)
    # and fans to expiration — NOT from open. Both edges follow the SAME √time progress
    # (slow now, accelerating toward expiry), fanning UP to max profit and DOWN to max
    # loss. Matching curves make the cone symmetric in shape, so the dollar asymmetry (a
    # 2:1 spread dives far steeper in red than it climbs in green) reads at a glance.
    xs_f = top = bot = None
    if now_x < dte0:
        n = 48
        xs_f = [now_x + (dte0 - now_x) * i / n for i in range(n + 1)]
        e_now = expected_pl_pct(dte0 - now_x, dte0)
        denom = (1.0 - e_now) or 1.0
        prog = [(expected_pl_pct(dte0 - x, dte0) - e_now) / denom for x in xs_f]
        top = [realized_now + (max_profit - realized_now) * p for p in prog]
        bot = [realized_now + (-max_loss - realized_now) * p for p in prog]

    # Backfill: if we started watching after open, bridge the gap with a straight, clearly
    # BLUE line from break-even at open ($0) to the first real reading. We know the entry
    # exactly (credit_received → $0 P/L), so this fabricates nothing — an honest estimate.
    xs_y = xs_real[:len(ys)]
    backfilled = bool(ys) and xs_y[0] > 0.5

    # Draw order (one color per braille cell): max-loss first (lowest priority), then
    # backfill, on-track, and realized LAST on top — so where lines share a cell, your
    # actual P/L wins. With the tall side-by-side cells they barely overlap anyway.
    if bot is not None:
        fig.plot(xs_f, bot, lc=C_WORST, label="max-loss")
    if backfilled:
        fig.plot([0.0, xs_y[0]], [0.0, ys[0]], lc=C_BACKFILL, label="backfill (est.)")
    if top is not None:
        fig.plot(xs_f, top, lc=C_SQRT, label="√time on-track")
    if ys:
        fig.plot(xs_y, ys, lc=C_REAL, label="realized P/L")

    dte_now = dte0 - now_x
    title = (f" P/L (up = made money)    max profit ${max_profit:g}   "
             f"max loss ${max_loss:g}   DTE {dte_now:.0f}/{dte0}")
    key_items = [("realized", C_REAL)]
    if backfilled:
        key_items.append(("backfill", C_BACKFILL))
    key_items += [("on-track √t (top)", C_SQRT), ("max-loss √t (bottom)", C_WORST)]
    return title + "\n " + _key(*key_items) + "\n" + fig.show()


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
    key = _key((f"{pos['underlying']} price", C_UNDER), ("profit edge", C_PROFIT),
               ("max-loss edge", C_LOSS), ("break-even", C_BE), ("cone→exp", C_CONE))
    return title + "\n " + key + "\n" + fig.show()


# ------------------------------------------------------------------------ IV / RV

def _day_of(open_dt: datetime, d: str) -> float:
    return (_parse(d) - open_dt).total_seconds() / 86400.0


def render_iv_rv_chart(pos: dict, history: list[dict],
                       closes: list[tuple[str, float]],
                       width: int = 90, height: int = 16,
                       window: int = DEFAULT_WINDOW) -> str:
    """Implied vol (forward, from our option snapshots) vs realized vol (backward, from
    the underlying's actual moves). Same units (annualized %), so they overlay on one Y.

    `closes` is the (date, close) series for the underlying (real bars, or — as a fallback
    for demos/new positions — the underlying_price from snapshot history)."""
    open_dt = _parse(pos["opened_at"])
    dte0 = pos.get("dte_at_open") or 1

    # IV series from snapshots (forward-only: we can't recover historical option IV)
    iv_x, iv_y = [], []
    for h in history:
        iv = h.get("iv_now")
        if iv is not None:
            iv_x.append(_day_of(open_dt, h["tick_at"]))
            iv_y.append(iv * 100)
    iv_now = iv_y[-1] if iv_y else (pos.get("iv_now") or 0) * 100
    iv_entry = (pos.get("iv_at_entry") or 0) * 100

    # RV series from underlying closes (real, can extend back before open)
    rv = rv_series(closes, window) if closes else []
    rv_x = [_day_of(open_dt, d) for d, _ in rv]
    rv_y = [v * 100 for _, v in rv]
    # keep only the position's life on the shared DTE axis (day 0 → expiry)
    rv_pts = [(x, y) for x, y in zip(rv_x, rv_y) if 0 <= x <= dte0]
    rv_now = realized_vol([c for _, c in closes], window) if closes else None
    rv_now_pct = rv_now * 100 if rv_now is not None else None

    allv = iv_y + [y for _, y in rv_pts] + ([iv_entry] if iv_entry else [])
    lo, hi = (min(allv), max(allv)) if allv else (0, 50)
    pad = (hi - lo) * 0.15 or 5.0

    fig = _new_fig(width, height, dte0, "vol %")
    fig.set_y_limits(min_=max(0, lo - pad), max_=hi + pad)
    fig.x_label = "days to exp"
    fig.x_ticks_fkt = lambda v, _: f"{dte0 - v:.0f}"

    if iv_entry:
        fig.plot([0, dte0], [iv_entry, iv_entry], lc=C_IVENTRY, label="IV@entry")
    if rv_pts:
        fig.plot([x for x, _ in rv_pts], [y for _, y in rv_pts], lc=C_RV, label="RV")
    if iv_x:
        fig.plot(iv_x, iv_y, lc=C_IV, label="IV")

    prem = (iv_now - rv_now_pct) if (rv_now_pct is not None) else None
    prem_txt = (f"  premium {prem:+.0f}pts ({'rich' if prem >= 0 else 'cheap'})"
                if prem is not None else "")
    title = (f" IV vs RV (annualized)   IV {iv_now:.0f}%   "
             f"RV{window}d {rv_now_pct:.0f}%" if rv_now_pct is not None
             else f" IV vs RV (annualized)   IV {iv_now:.0f}%   RV n/a")
    key = _key(("IV (implied)", C_IV), ("RV (realized)", C_RV), ("IV@entry", C_IVENTRY))
    return title + prem_txt + "\n " + key + "\n" + fig.show()
