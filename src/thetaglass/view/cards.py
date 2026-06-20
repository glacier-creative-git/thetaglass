"""The dense per-position card shown in the monitor's bottom list (Layer E2).

Each open position is a compact multi-line grid: identity + the headline P/L, then the
diagnostics (DTE, on-track vs expected, cushion to strike, IV regime, Greeks, health
axes). One card per row in the navigable list; the highlighted one drives the chart.
"""
from __future__ import annotations

from rich.table import Table
from rich.text import Text

from thetaglass.view.overview import _label, health_color


def _pct(x, signed=False) -> str:
    if x is None:
        return "—"
    return f"{x * 100:+.1f}%" if signed else f"{x * 100:.1f}%"


def _money(x) -> str:
    return "—" if x is None else f"${x:+,.0f}"


def render_position_card(pos: dict) -> Table:
    health = pos.get("health_score")
    hc = health_color(health)
    tag = " [reverse] MOCK [/reverse]" if pos.get("is_mock") else ""

    grid = Table.grid(padding=(0, 1), expand=True)
    grid.add_column(justify="left", ratio=1)
    grid.add_column(justify="right")

    # line 1 — identity + headline P/L + health
    title = Text(_label(pos), style="bold")
    pl = pos.get("pl_dollars")
    pl_txt = Text(f"{_money(pl)} ", style="green" if (pl or 0) >= 0 else "red")
    pl_txt.append(f"({_pct(pos.get('pl_pct_of_max_profit'))} of max)", style="dim")
    grid.add_row(Text.assemble(title, Text.from_markup(tag)), pl_txt)

    # line 2 — schedule + cushion
    on_track = "AHEAD" if (pos.get("pl_pct_of_max_profit") or 0) >= (pos.get("expected_pl_pct") or 0) else "BEHIND"
    left = Text.assemble(
        ("DTE ", "dim"), (f"{pos.get('dte_remaining')}/{pos.get('dte_at_open')}", ""),
        ("   expected ", "dim"), (_pct(pos.get("expected_pl_pct")), ""),
        ("  ", ""), (on_track, "green" if on_track == "AHEAD" else "yellow"),
    )
    right = Text.assemble(("dist→K ", "dim"),
                          (_pct(pos.get("distance_to_short_strike_pct")),
                           health_color(pos.get("health_score"))))
    grid.add_row(left, right)

    # line 3 — greeks + IV regime
    g = Text.assemble(
        ("Δ", "dim"), (f"{_num(pos.get('net_delta'))} ", ""),
        ("Θ", "dim"), (f"{_num(pos.get('net_theta'))} ", ""),
        ("V", "dim"), (f"{_num(pos.get('net_vega'))}", ""),
    )
    iv = Text.assemble(("IV ", "dim"),
                       (f"{_fnum(pos.get('iv_now'))}/{_fnum(pos.get('iv_at_entry'))} ", ""),
                       (_pct(pos.get("iv_regime_delta_pct"), signed=True), "dim"))
    grid.add_row(g, iv)

    # line 4 — health axes broken out
    axes = pos.get("health_axes") or {}
    ax = Text.assemble(
        ("health ", "dim"), (f"{health:.2f}" if health is not None else "—", f"bold {hc}"),
        ("   θ-track ", "dim"), (_axis(axes.get("theta_on_track")), ""),
        ("  strike ", "dim"), (_axis(axes.get("strike_distance")), ""),
        ("  iv ", "dim"), (_axis(axes.get("iv_stability")), ""),
    )
    grid.add_row(ax, Text(""))
    return grid


def _num(x) -> str:
    return "—" if x is None else f"{x:+.2f}"


def _fnum(x) -> str:
    return "—" if x is None else f"{x:.3f}"


def _axis(x) -> str:
    return "—" if x is None else f"{x:.2f}"
