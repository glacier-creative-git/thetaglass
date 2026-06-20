"""The Gantt overview renderer — content and the shared-axis invariant.

Pure rendering over Position dicts, so we can assert the drawn text without a terminal.
"""
from datetime import datetime, timezone
from io import StringIO

from rich.console import Console

from thetaglass.view import render_overview
from thetaglass.view.overview import ELAPSED, REMAIN

NOW = datetime(2026, 6, 20, tzinfo=timezone.utc)


def _pos(sym, opened, exp, pl=10.0, theta=0.05, health=0.8):
    return {"underlying": sym, "strategy_type": "put_credit_spread",
            "opened_at": opened, "pl_dollars": pl, "net_theta": theta,
            "health_score": health,
            "legs": [{"side": "short", "strike": 100, "option_type": "put", "expiration": exp},
                     {"side": "long", "strike": 95, "option_type": "put", "expiration": exp}]}


def _txt(renderable, width=120) -> str:
    c = Console(width=width, file=StringIO(), force_terminal=False)
    c.print(renderable)
    return c.file.getvalue()


def test_empty_message():
    assert "No open positions" in _txt(render_overview([]))


def test_renders_label_and_axis():
    out = _txt(render_overview([_pos("QQQ", "2026-06-01T00:00:00Z", "2026-07-17")], now=NOW))
    assert "QQQ 100/95 put credit" in out
    assert "NOW" in out
    assert ELAPSED in out and REMAIN in out          # both elapsed and runway drawn
    assert "Jul-17" in out                            # expiration shown


def test_elapsed_grows_with_age():
    # A position opened long ago should show more solid bar than a fresh one, on the
    # same shared axis — that's the whole point of the timeline.
    old = _pos("OLD", "2026-05-01T00:00:00Z", "2026-08-01")
    new = _pos("NEW", "2026-06-19T00:00:00Z", "2026-08-01")
    out = _txt(render_overview([old, new], now=NOW), width=120)
    old_line = next(l for l in out.splitlines() if "OLD" in l)
    new_line = next(l for l in out.splitlines() if "NEW" in l)
    assert old_line.count(ELAPSED) > new_line.count(ELAPSED)


def test_now_marker_present_once_in_header():
    out = _txt(render_overview([_pos("QQQ", "2026-06-01T00:00:00Z", "2026-07-17")], now=NOW))
    assert out.count("NOW") == 1
