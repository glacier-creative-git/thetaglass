"""The `tg status` overview — a Gantt timeline of every open position (Layer E1).

One row per position, spanning [opened_at → expiration] on a SHARED, absolute time axis,
so the eye reads relative life-stage and clustering at a glance. The elapsed portion is a
solid health-colored bar; the remaining life is a dim dashed tail (the decay runway). A
single NOW marker lines up across every row.

Pure rendering: takes the canonical Position dicts (straight from positions_current) and
returns a Rich renderable. No store, no network — so it's trivially testable and works
headless/over SSH, which is exactly why the overview is Rich and not Textual.
"""
from __future__ import annotations

from datetime import datetime, timezone

from rich.console import Group
from rich.text import Text

# Short, human labels for the strategy tag in each row.
_STRAT_SHORT = {
    "put_credit_spread": "put credit", "call_credit_spread": "call credit",
    "put_debit_spread": "put debit", "call_debit_spread": "call debit",
    "iron_condor": "iron condor", "naked_short_put": "short put",
    "naked_short_call": "short call", "long_put": "long put",
    "long_call": "long call", "custom_multi_leg": "multi-leg",
}

ELAPSED = "━"   # solid: time already lived (health-colored)
REMAIN = "╌"    # dashed: the decay runway still ahead (dim)


def health_color(h: float | None) -> str:
    if h is None:
        return "grey50"
    return "green" if h >= 0.7 else "yellow" if h >= 0.4 else "red"


def _parse(ts: str) -> datetime:
    """Parse an ISO timestamp (date or datetime, 'Z' or offset) to aware UTC."""
    s = ts.replace("Z", "+00:00")
    if len(s) == 10:                       # bare date → end of that day
        s += "T23:59:59+00:00"
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _label(p: dict) -> str:
    short = next((l for l in p["legs"] if l["side"] == "short"), None)
    long = next((l for l in p["legs"] if l["side"] == "long"), None)
    strat = _STRAT_SHORT.get(p["strategy_type"], p["strategy_type"])
    if short and long:
        strikes = f"{short['strike']:g}/{long['strike']:g}"
    elif short:
        strikes = f"{short['strike']:g}"
    else:
        strikes = ""
    return f"{p['underlying']} {strikes} {strat}".strip()


def _put(buf: list[str], col: int, s: str) -> None:
    """Drop string s into a char buffer starting at col (clamped to the buffer)."""
    for i, ch in enumerate(s):
        c = col + i
        if 0 <= c < len(buf):
            buf[c] = ch


def render_overview(positions: list[dict], now: datetime | None = None,
                    width: int = 100) -> Group:
    if not positions:
        return Group(Text("No open positions. Run `tg sync` (or start the timekeeper).",
                          style="yellow"))
    now = now or datetime.now(timezone.utc)

    rows = []
    for p in positions:
        exp = _parse(p["legs"][0]["expiration"])
        rows.append({"p": p, "open": _parse(p["opened_at"]), "exp": exp,
                     "label": _label(p)})

    # Left metadata block — pad every field to a common width so bars align. Kept tight
    # so the time axis gets the room; the column headers live in the axis header line.
    lbl_w = max(len(r["label"]) for r in rows)
    metas = []
    for r in rows:
        p = r["p"]
        meta = (f"{r['label']:<{lbl_w}} {r['exp']:%b-%d} "
                f"{_money(p['pl_dollars']):>6} θ{_num(p['net_theta']):>6} "
                f"H{_hstr(p['health_score'])} ")
        metas.append(meta)
    meta_w = max(len(m) for m in metas)
    axis_w = max(24, width - meta_w)

    # Shared absolute axis across all positions.
    t_min = min(r["open"] for r in rows)
    t_max = max(r["exp"] for r in rows)
    span = (t_max - t_min).total_seconds() or 1.0

    def col(t: datetime) -> int:
        frac = (t - t_min).total_seconds() / span
        return max(0, min(axis_w - 1, round(frac * (axis_w - 1))))

    now_col = col(now)

    # Header: the axis line with date anchors and the NOW marker. The end-date labels
    # yield to the NOW caret when they'd collide (e.g. a just-opened position).
    head = [" "] * axis_w
    if now_col > 6:
        _put(head, 0, f"{t_min:%b-%d}")
    if now_col < axis_w - 7:
        _put(head, axis_w - 6, f"{t_max:%b-%d}")
    _put(head, now_col, "│")
    header = Text(" " * meta_w)
    header.append("".join(head), style="dim")
    nowlbl = Text(" " * (meta_w + max(0, now_col - 1)))
    nowlbl.append("NOW", style="bold cyan")

    body = [nowlbl, header]
    for r, meta in zip(rows, metas):
        p = r["p"]
        color = health_color(p["health_score"])
        c0, c_now, c1 = col(r["open"]), now_col, col(r["exp"])
        bar = Text(f"{meta:<{meta_w}}")
        for c in range(axis_w):
            if c0 <= c < c_now:
                bar.append(ELAPSED, style=f"bold {color}")
            elif c_now <= c <= c1:
                bar.append(REMAIN, style="grey50")
            else:
                bar.append(" ")
        body.append(bar)
    return Group(*body)


def _money(x: float | None) -> str:
    return "—" if x is None else f"${x:+,.0f}"


def _num(x: float | None) -> str:
    return "—" if x is None else f"{x:+.2f}"


def _hstr(h: float | None) -> str:
    return "—" if h is None else f"{h:.2f}"
