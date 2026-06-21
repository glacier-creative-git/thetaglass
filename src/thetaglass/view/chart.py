"""Per-position drill-down charts (Layer E2) — two INDEPENDENT plotille cells.

Underlying price and position P/L can't share a Y axis honestly, so we don't try: each
is its own self-scaled chart with full vertical room. Both share only the conceptual X
(days since open). plotille draws rgb braille and returns a string, so each slots into
its own Textual cell via Text.from_ansi.

  render_pnl_chart        — P/L in dollars (up = made money): unrealized (mark-to-market)
                            path + forecast cone (√time on-track top, √time max-loss bottom).
  render_underlying_chart — underlying price + the outcome edges (short/long strike,
                            break-even) and a forward cone projecting today's price to
                            those edges at expiration.

Pure: each takes a Position dict + snapshot history, returns an ANSI string.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

import plotille

from thetaglass.settings import CONFIG
from thetaglass.state.baseline import expected_pl_pct
from thetaglass.state.blackscholes import underlying_for_profit
from thetaglass.view import logo as _logo

# rgb line colors (plotille color_mode="rgb")
C_UNDER = (80, 150, 245)     # underlying daily-close price — blue
C_PROFIT = (95, 200, 120)    # profit edge (short strike) — green
C_LOSS = (215, 95, 95)       # max-loss edge (long strike) — red
C_BE_GREY = (155, 155, 170)  # break-even — grey
C_UNREAL = (95, 150, 245)    # unrealized (mark-to-market) P/L, incl. pre-watch bridge — blue
C_BACKFILL = C_UNREAL        # bridge is the same blue: one continuous P/L line
C_SQRT = (95, 165, 110)      # √time on-track (cone top) — dark green
C_WORST = (200, 115, 115)    # linear → max loss (cone bottom) — dim red
C_IV_GOOD = (95, 205, 130)   # IV below entry — vol falling, good for the seller — green
C_IV_BAD = (220, 100, 100)   # IV above entry — vega working against you — red
C_IVENTRY = (150, 150, 165)  # IV-at-entry reference line — grey


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
    fig.x_label = "DTE"
    fig.x_ticks_fkt = lambda v, _: f"{dte0 - v:.0f}"

    ys = [h["pl_dollars"] for h in history if h.get("pl_dollars") is not None]
    unrealized_now = ys[-1] if ys else (pos.get("pl_dollars") or 0.0)

    # The cone is a FORECAST: it starts at NOW (anchored to the current unrealized value)
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
        top = [unrealized_now + (max_profit - unrealized_now) * p for p in prog]
        bot = [unrealized_now + (-max_loss - unrealized_now) * p for p in prog]

    # Backfill: if we started watching after open, bridge the gap with a straight, clearly
    # BLUE line from break-even at open ($0) to the first real reading. We know the entry
    # exactly (credit_received → $0 P/L), so this fabricates nothing — an honest estimate.
    xs_y = xs_real[:len(ys)]
    backfilled = bool(ys) and xs_y[0] > 0.5

    # Draw order (one color per braille cell): max-loss first (lowest priority), then
    # backfill, on-track, and unrealized LAST on top — so where lines share a cell, your
    # actual P/L wins. With the tall side-by-side cells they barely overlap anyway.
    if bot is not None:
        fig.plot(xs_f, bot, lc=C_WORST, label="max-loss")
    if backfilled:
        fig.plot([0.0, xs_y[0]], [0.0, ys[0]], lc=C_BACKFILL, label="backfill (est.)")
    if top is not None:
        fig.plot(xs_f, top, lc=C_SQRT, label="√time on-track")
    if ys:
        fig.plot(xs_y, ys, lc=C_UNREAL, label="unrealized P/L")

    dte_now = dte0 - now_x
    title = (f" P/L (up = made money)    max profit ${max_profit:g}   "
             f"max loss ${max_loss:g}   DTE {dte_now:.0f}/{dte0}")
    # The position is still OPEN — this P/L is mark-to-market, i.e. UNrealized — and the
    # pre-watch bridge shares its blue, so the key shows one "unrealized P/L" entry.
    key_items = [("unrealized P/L", C_UNREAL),
                 ("on-track √t (top)", C_SQRT), ("max-loss √t (bottom)", C_WORST)]
    return title + "\n " + _key(*key_items) + "\n" + fig.show()


# -------------------------------------------------------------------- underlying

def render_underlying_chart(pos: dict, history: list[dict],
                            closes: list[tuple[str, float]],
                            width: int = 90, height: int = 16) -> str:
    """The underlying's REAL daily-close price (blue) against the three levels that decide
    the trade: profit edge (short strike, green), max-loss edge (long strike, red), and
    break-even (grey). Unlike P/L and IV, the underlying's pre-watch history is real and
    backfillable — so the line is just the true price, no estimated bridge."""
    open_dt = _parse(pos["opened_at"])
    dte0 = pos.get("dte_at_open") or 1
    legs = pos["legs"]
    short = next((l for l in legs if l["side"] == "short"), legs[0])

    # real daily closes from open onward, plus the live spot so the line reaches NOW. The
    # open-day bar is timestamped 00:00 (before an intraday fill), so filter by DATE and
    # clamp that bar to day 0 rather than letting it fall negative and get dropped.
    open_date = (pos.get("opened_at") or "")[:10]
    pts = [(max(0.0, _day_of(open_dt, d)), c) for d, c in (closes or []) if d >= open_date]
    now_x = dte0 - (pos.get("dte_remaining") or 0)
    spot = pos.get("underlying_price")
    if spot and (not pts or now_x > pts[-1][0] + 0.5):
        pts.append((now_x, spot))
    spot = spot or (pts[-1][1] if pts else short["strike"])

    prices = [c for _, c in pts]
    iv = pos.get("iv_now") or pos.get("iv_at_entry") or 0.25
    credit = pos.get("credit_received") or 0.0
    max_profit = pos.get("max_profit") or credit or 1.0

    # Profit-% edges, from NOW to expiry: what the underlying must reach, EACH day, for the
    # spread to be worth X% of max profit (Black-Scholes run backwards, flat IV). They start
    # spread out at NOW and converge toward the strikes at expiry — watch the price line
    # climb toward the green (max-profit) edge: when it touches, you can close for ~max.
    edge_specs = [(0.9, C_PROFIT), (0.5, C_BE_GREY), (0.0, C_LOSS)]
    n = 32
    curves = []
    for f, color in edge_specs:
        cur = []
        for i in range(n + 1):
            x = now_x + (dte0 - now_x) * i / n
            s = underlying_for_profit(legs, credit, max_profit, f,
                                      max(0.0, dte0 - x) / 365.0, iv)
            if s is not None:
                cur.append((x, s))
        curves.append((cur, color))

    edge_ys = [s for cur, _ in curves for _, s in cur]
    ys_all = prices + edge_ys + [spot]
    lo, hi = min(ys_all), max(ys_all)
    pad = (hi - lo) * 0.08 or 1.0

    fig = _new_fig(width, height, dte0, "$")
    fig.set_y_limits(min_=lo - pad, max_=hi + pad)
    fig.x_label = "DTE"
    fig.x_ticks_fkt = lambda v, _: f"{dte0 - v:.0f}"

    for cur, color in curves:
        if cur:
            fig.plot([x for x, _ in cur], [s for _, s in cur], lc=color)
    # the real price line (blue), drawn last so it sits on top
    if pts:
        fig.plot([x for x, _ in pts], prices, lc=C_UNDER)

    g_now = curves[0][0][0][1] if curves[0][0] else spot   # 90% edge at NOW
    title = (f" UNDERLYING  {pos['underlying']}  spot ${spot:g}   "
             f"max-profit edge ${g_now:.0f}→{short['strike']:g} (now→exp)")
    key = _key((f"{pos['underlying']} price", C_UNDER), ("90% (max) profit", C_PROFIT),
               ("50% profit", C_BE_GREY), ("break-even 0%", C_LOSS))
    return title + "\n " + key + "\n" + fig.show()


# ----------------------------------------------------------- implied volatility

def _day_of(open_dt: datetime, d: str) -> float:
    return (_parse(d) - open_dt).total_seconds() / 86400.0


def render_iv_chart(pos: dict, history: list[dict],
                    width: int = 90, height: int = 16) -> str:
    """Implied volatility since we opened, vs the IV we sold at.

    When you SELL options you want IV to fall: below entry (green) the premium is bleeding
    out in your favor; above entry (red) vega is working against you — your short options
    can gain value even if the underlying cooperates. That vega story is the whole point,
    and it needs only data we have (per-tick IV + iv_at_entry) — no horizon-matching, no
    historical option IV (which isn't recoverable anyway)."""
    open_dt = _parse(pos["opened_at"])
    dte0 = pos.get("dte_at_open") or 1
    iv_entry = (pos.get("iv_at_entry") or 0) * 100

    pts = [(_day_of(open_dt, h["tick_at"]), h["iv_now"] * 100)
           for h in history if h.get("iv_now") is not None]
    iv_now = pts[-1][1] if pts else (pos.get("iv_now") or 0) * 100

    allv = [y for _, y in pts] + ([iv_entry] if iv_entry else [iv_now])
    lo, hi = min(allv), max(allv)
    pad = (hi - lo) * 0.25 + 2.0

    fig = _new_fig(width, height, dte0, "IV %")
    fig.set_y_limits(min_=max(0, lo - pad), max_=hi + pad)
    fig.x_label = "DTE"
    fig.x_ticks_fkt = lambda v, _: f"{dte0 - v:.0f}"

    # the line you sold at — everything is read relative to this
    if iv_entry:
        fig.plot([0, dte0], [iv_entry, iv_entry], lc=C_IVENTRY)

    # Blue backfill: from the true entry IV (BS-inverted from the real fill) at open to the
    # first IV we actually observed — a real interpolation between two real endpoints, same
    # spirit as the P/L backfill bridge.
    backfilled = bool(pts) and pts[0][0] > 0.5 and iv_entry > 0
    if backfilled:
        fig.plot([0.0, pts[0][0]], [iv_entry, pts[0][1]], lc=C_BACKFILL)

    # IV path, colored per segment: green below entry (good), red above (bad)
    for i in range(1, len(pts)):
        (x0, y0), (x1, y1) = pts[i - 1], pts[i]
        good = (y0 + y1) / 2 <= iv_entry
        fig.plot([x0, x1], [y0, y1], lc=C_IV_GOOD if good else C_IV_BAD)
    if len(pts) == 1:                                   # a single sighting → a dot
        x0, y0 = pts[0]
        fig.plot([x0, x0], [y0, y0], lc=C_IV_GOOD if y0 <= iv_entry else C_IV_BAD)

    delta = iv_now - iv_entry
    verdict = ("vol ≈ flat" if abs(delta) < 0.5
               else "vol DOWN — premium decaying" if delta < 0
               else "vol UP — vega against you")
    title = (f" IMPLIED VOL vs entry   now {iv_now:.0f}%   entry {iv_entry:.0f}%   "
             f"Δ {delta:+.0f}pts   {verdict}")
    key_items = [("IV < entry (good)", C_IV_GOOD), ("IV > entry (bad)", C_IV_BAD)]
    if backfilled:
        key_items.append(("backfill", C_BACKFILL))
    key_items.append(("IV@entry", C_IVENTRY))
    return title + "\n " + _key(*key_items) + "\n" + fig.show()


# --------------------------------------------------------------------- health score

# health-cell palette (rgb); reused by the score banner + axis bars
C_HEALTHY = (95, 205, 130)   # ≥ 0.7 — green
C_WATCH = (220, 190, 90)     # 0.4–0.7 — amber
C_BAD = (220, 100, 100)      # < 0.4, or any axis below CRIT — red
C_DIM = (70, 70, 82)         # empty bar track — dim grey
C_MUT = (150, 150, 165)      # muted labels/footnote — grey

# How many discrete sand levels the hourglass shows across a position's life. 10 (one per
# 10%) is the most the compact mark's braille resolution renders as distinct steps — each
# tenth flips at least one dot as the top drains and the bottom fills.
_SAND_STEPS = 10

# Which raw axis each health component normalizes, and its weight in the blend — shown on
# the bar so the weighting (the thing that's easy to misread) is explicit.
_AXES = [
    ("theta_on_track", "θ-track", CONFIG.W_THETA),
    ("strike_distance", "strike", CONFIG.W_STRIKE),
    ("iv_stability", "iv", CONFIG.W_IV),
]


def _c(s: str, rgb: tuple[int, int, int]) -> str:
    r, g, b = rgb
    return f"\x1b[38;2;{r};{g};{b}m{s}\x1b[0m"


def _score_color(h: float | None) -> tuple[int, int, int]:
    if h is None:
        return C_MUT
    return C_HEALTHY if h >= 0.7 else C_WATCH if h >= 0.4 else C_BAD


def _axis_color(v: float) -> tuple[int, int, int]:
    # red the instant an axis is critical (it's what trips the weakest-link floor)
    return C_BAD if v < CONFIG.CRIT else C_WATCH if v < 0.7 else C_HEALTHY


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _vlen(s: str) -> int:
    """Visible length of an ANSI string (escape codes don't take screen columns)."""
    return len(_ANSI_RE.sub("", s))


def _center(line: str, inner: int) -> str:
    return " " * max(0, (inner - _vlen(line)) // 2) + line


def _frame(lines: list[str], width: int, height: int, hcenter: bool) -> str:
    """Center a block of (possibly ANSI) lines within the cell — vertically always, and
    horizontally when hcenter. Unlike the plotille charts (locked top-left), the health
    cell is plain text, so we can pad it to sit in the middle as the terminal resizes."""
    if hcenter:
        bw = max((_vlen(l) for l in lines), default=0)
        lp = " " * max(0, (width - bw) // 2)
        lines = [lp + l for l in lines]
    top = max(0, (height - len(lines)) // 2)
    return "\n".join([""] * top + lines)


def _bar_rows(vals: dict, weakest, floored: bool, bar_w: int) -> list[str]:
    rows = []
    for key, lab, w in _AXES:
        v = vals.get(key)
        head_cell = _c(f"{lab:<7} {int(w * 100):>2}%", C_MUT)   # fixed-width label + weight
        if v is None:
            rows.append(f"{head_cell}   " + _c("—", C_MUT))
            continue
        filled = int(round(max(0.0, min(1.0, v)) * bar_w))
        bar = _c("█" * filled, _axis_color(v)) + _c("░" * (bar_w - filled), C_DIM)
        flag = _c("  ◄", C_BAD if floored else C_MUT) if key == weakest else ""
        rows.append(f"{head_cell}   {bar}  {v:.2f}{flag}")
    return rows


def _life_elapsed(pos: dict) -> float | None:
    """Fraction of the position's life that has elapsed since it opened (0 at open, 1 at
    expiration) — i.e. how far it's run through its theta decay. None if DTE is unknown."""
    opened = pos.get("dte_at_open")
    left = pos.get("dte_remaining")
    if not opened or left is None:
        return None
    return max(0.0, min(1.0, (opened - left) / opened))


def _dte_row(pos: dict, elapsed: float, bar_w: int) -> str:
    """A progress bar for the position's life — same shape as the axis bars but neutral (grey,
    not health-colored): it tracks time, not good/bad. Mirrors the hourglass sand level."""
    opened, left = pos.get("dte_at_open"), pos.get("dte_remaining")
    head = _c(f"{'DTE':<11}", C_MUT)            # blank weight slot keeps the bars aligned
    filled = int(round(max(0.0, min(1.0, elapsed)) * bar_w))
    bar = _c("█" * filled, C_MUT) + _c("░" * (bar_w - filled), C_DIM)
    return f"{head}   {bar}  " + _c(f"{left:>2}/{opened}d {int(round(elapsed * 100)):>2}%", C_MUT)


def render_health_chart(pos: dict, width: int = 90, height: int = 16) -> str:
    """The weighted health score as a SNAPSHOT scoreboard, with the Thetaglass mark beside
    it. The other three cells already carry each axis's history, so this cell is the brand +
    the blended number + the three 0–1 axes as bars (weakest-link floor flagged). The θ /
    hourglass logo sits on the left; score and bars stack on the right. The hourglass sand is
    a real readout: it shows how far the position has run through its life (top full at open,
    drained to the bottom by expiration), in 10% steps."""
    health = pos.get("health_score")
    axes = pos.get("health_axes") or {}
    if health is None or not axes:
        return _c("  health score unavailable\n  (needs price + IV to compute its axes)", C_MUT)

    vals = {k: axes.get(k) for k, _lab, _w in _AXES if axes.get(k) is not None}
    weakest = min(vals, key=vals.get) if vals else None
    floored = bool(vals) and vals[weakest] < CONFIG.CRIT
    sc = _score_color(health)
    verdict = ("CRITICAL" if (health < 0.4 or floored) else
               "WATCH" if health < 0.7 else "HEALTHY")
    floor_note = ""
    if floored:
        floor_note = _c(f"⚠ floored by {dict((k, l) for k, l, _ in _AXES)[weakest]}", C_BAD)

    # Sand level = how far the position has run through its life, snapped to _SAND_STEPS. The
    # hourglass empties from the top as expiration nears: fill_bottom rises, fill_top falls.
    elapsed = _life_elapsed(pos)
    snapped = round(elapsed * _SAND_STEPS) / _SAND_STEPS if elapsed is not None else None

    # Show the mark only when the cell is roomy enough; otherwise center the scoreboard.
    if width >= 52 and height >= 13 and snapped is not None:
        logo = list(_logo.compact_frame(1.0 - snapped, snapped))
    else:
        logo = None
    logo_w = _vlen(logo[0]) if logo else 0
    inner = max(28, width - logo_w - 3)
    bar_w = max(10, min(26, inner - 24))
    bars = _bar_rows(vals, weakest, floored, bar_w)
    if elapsed is not None:
        bars.append(_dte_row(pos, elapsed, bar_w))
    footnote = _c(f"floor: 1 axis < {CONFIG.CRIT:g} caps the score", C_MUT)

    if not logo:
        block_w = max(_vlen(r) for r in bars)
        m = " " * max(0, (width - block_w) // 2)
        head = _c("HEALTH", C_MUT) + "   " + _c(f"{health:.2f}", sc) + "   " + _c(verdict, sc)
        lines = [_center(head, width), ""]
        if floored:
            lines.append(_center(floor_note, width))
        lines += [m + r for r in bars] + ["", _center(footnote, width)]
        return _frame(lines, width, height, hcenter=False)

    # right column: the score on top, the axis bars below
    right = [_c("T H E T A G L A S S", C_MUT), "",
             _c(f"{health:.2f}", sc) + "   " + _c(verdict, sc)]
    if floored:
        right.append(floor_note)
    right += [""] + bars + ["", footnote]

    offset = max(0, (len(logo) - len(right)) // 2)
    total = max(len(logo), offset + len(right))
    out = []
    for i in range(total):
        left = logo[i] if i < len(logo) else " " * logo_w
        j = i - offset
        rline = right[j] if 0 <= j < len(right) else ""
        out.append(f"{left}   {rline}")
    return _frame(out, width, height, hcenter=True)
