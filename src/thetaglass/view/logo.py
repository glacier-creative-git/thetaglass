"""The Thetaglass mark — a Greek θ with a curvy-X hourglass (and sand) inscribed inside it.

Rendered in braille (U+2800–U+28FF), the same technique the Hermes agent CLI uses for its
caduceus: a high-resolution dot bitmap (2×4 dots per character cell) packed into braille
codepoints, then tinted with a metallic bronze→gold→bronze vertical gradient.

The θ carries its weight on the SIDES (thin top/bottom), matching the real glyph. The
hourglass is two concave walls that sweep from the oval into a pinched neck — a "curvy X" —
with dense sand floating inside each chamber. `fill_top`/`fill_bottom` set the sand level
per chamber (the lever the future animation drives to move sand back and forth).

  render_mark(variant) → colored mark as a multi-line ANSI string ("V22" | "V20" | "compact")
  mark_lines(variant)  → the raw braille rows (no color)
  python -m thetaglass.view.logo [V20|V22|compact] [--plain]   # cat it in the terminal

The geometry is generated, so every dimension is a tunable knob; the three presets below
differ only in scale + a few gaps. "compact" is sized to sit inside the monitor's cell.
"""
from __future__ import annotations

import math
import sys
from functools import lru_cache

# Per-preset geometry (px space is 2×4 the braille-cell grid). idx/idy inset the oval to
# give the inner radii the hourglass + sand attach to.
_GEOM = {
    "V22":     dict(W=54, H=68, ax=22, ay=29, base=0.70, amp=1.40, neck=2.5, margin=1.0,
                    side_m=3.0, neck_gap=3.0, idx=2.5, idy=1.5, fy=0.74),
    "V20":     dict(W=54, H=68, ax=20, ay=29, base=0.70, amp=1.40, neck=2.5, margin=1.0,
                    side_m=2.5, neck_gap=2.5, idx=2.5, idy=1.5, fy=0.74),
    "compact": dict(W=44, H=52, ax=18, ay=24, base=0.65, amp=1.25, neck=1.9, margin=0.9,
                    side_m=2.4, neck_gap=2.6, idx=2.0, idy=1.2, fy=0.74),
}

# metallic gradient stops (vertical fraction → rgb), per the Hermes palette
_STOPS = [(0.0, (205, 127, 50)), (0.28, (255, 191, 0)), (0.5, (255, 215, 0)),
          (0.72, (255, 191, 0)), (0.88, (205, 127, 50)), (1.0, (184, 134, 11))]

_DOT = [[0x01, 0x08], [0x02, 0x10], [0x04, 0x20], [0x40, 0x80]]   # [dy][dx] → bit
_BLANK = "⠀"


def _blank(w: int, h: int) -> list[list[int]]:
    return [[0] * w for _ in range(h)]


def _px(g, x, y) -> None:
    xi, yi = int(round(x)), int(round(y))
    if 0 <= yi < len(g) and 0 <= xi < len(g[0]):
        g[yi][xi] = 1


def _to_braille(g) -> list[str]:
    h, w = len(g), len(g[0])
    out = []
    for cy in range(0, h, 4):
        line = ""
        for cx in range(0, w, 2):
            v = 0
            for dy in range(4):
                for dx in range(2):
                    if cy + dy < h and cx + dx < w and g[cy + dy][cx + dx]:
                        v |= _DOT[dy][dx]
            line += chr(0x2800 + v)
        out.append(line.rstrip(_BLANK) or _BLANK)
    return out


def _theta_ring(g, cx, cy, ax, ay, base, amp) -> None:
    """Oval ring, stroke thick on the sides and thin at top/bottom (real θ stress)."""
    for y in range(len(g)):
        for x in range(len(g[0])):
            nx, ny = (x - cx) / ax, (y - cy) / ay
            e = nx * nx + ny * ny
            gm = 2 * math.hypot(nx / ax, ny / ay) or 1e-9
            cos = abs(nx) / math.sqrt(e + 1e-9)        # ~|cos|: 1 at sides, 0 top/bottom
            if abs(e - 1) / gm < base + amp * cos * cos and e < 1.3:
                g[y][x] = 1


def _hw(y, cy, half, neck, w_end):
    v = (y - cy) / half
    return neck + (w_end - neck) * math.sin(min(1.0, abs(v)) * math.pi / 2)


def _hourglass(g, cx, cy, ax_in, ay_in, fy, neck, margin) -> None:
    """Two concave sine-profile walls from the oval ring into a pinched neck — the curvy X."""
    top_y, bot_y = cy - fy * ay_in, cy + fy * ay_in
    w_end = ax_in * math.sqrt(1 - fy * fy) - margin
    half = fy * ay_in
    steps = int((bot_y - top_y) * 6)
    for i in range(steps + 1):
        y = top_y + (bot_y - top_y) * i / steps
        hw = _hw(y, cy, half, neck, w_end)
        _px(g, cx - hw, y)
        _px(g, cx + hw, y)


def _sand(g, cx, cy, ax_in, ay_in, fill_top, fill_bottom, side_m, neck_gap, fy, neck, margin):
    """Dense sand floating inside each chamber. fill_top/fill_bottom (0..1) set how full each
    chamber is; the top surface drops as it drains, the bottom pile rises as it fills. The
    lateral gap to the glass walls (side_m) is fixed and never changes with the fill."""
    top_y, bot_y = cy - fy * ay_in, cy + fy * ay_in
    w_end = ax_in * math.sqrt(1 - fy * fy) - margin
    half = fy * ay_in

    def fill(y0, y1):
        yy = int(round(y0))
        while yy <= int(round(y1)):
            inner = _hw(yy, cy, half, neck, w_end) - side_m
            if inner > 0:
                for xx in range(int(round(cx - inner)), int(round(cx + inner)) + 1):
                    if 0 <= yy < len(g) and 0 <= xx < len(g[0]):
                        g[yy][xx] = 1
            yy += 1

    top_lo = cy - neck_gap
    fill(top_y + (1 - fill_top) * (top_lo - top_y), top_lo)      # top: surface drops
    bot_hi = cy + neck_gap
    fill(bot_y - fill_bottom * (bot_y - bot_hi), bot_y)          # bottom: pile rises


def _build(geo, fill_top, fill_bottom) -> list[str]:
    g = _blank(geo["W"], geo["H"])
    cx, cy = (geo["W"] - 1) / 2, (geo["H"] - 1) / 2
    _theta_ring(g, cx, cy, geo["ax"], geo["ay"], geo["base"], geo["amp"])
    ax_in, ay_in = geo["ax"] - geo["idx"], geo["ay"] - geo["idy"]
    _hourglass(g, cx, cy, ax_in, ay_in, geo["fy"], geo["neck"], geo["margin"])
    _sand(g, cx, cy, ax_in, ay_in, fill_top, fill_bottom,
          geo["side_m"], geo["neck_gap"], geo["fy"], geo["neck"], geo["margin"])
    return _to_braille(g)


def mark_lines(variant: str = "V22", fill_top: float = 1.0, fill_bottom: float = 1.0,
               trim: bool = False) -> list[str]:
    """The mark as raw braille rows (no color). `trim` drops blank top/bottom rows."""
    rows = _build(_GEOM.get(variant, _GEOM["V22"]), fill_top, fill_bottom)
    if trim:
        while rows and rows[0].strip(_BLANK) == "":
            rows.pop(0)
        while rows and rows[-1].strip(_BLANK) == "":
            rows.pop()
    return rows


def _metal(f: float) -> tuple[int, int, int]:
    for i in range(len(_STOPS) - 1):
        f0, c0 = _STOPS[i]
        f1, c1 = _STOPS[i + 1]
        if f0 <= f <= f1:
            t = (f - f0) / (f1 - f0) if f1 > f0 else 0.0
            return tuple(round(a + (b - a) * t) for a, b in zip(c0, c1))
    return _STOPS[-1][1]


def colorize(rows: list[str]) -> str:
    """Apply the metallic vertical gradient (one rgb per row) as ANSI truecolor."""
    n = len(rows)
    out = []
    for i, row in enumerate(rows):
        r, g, b = _metal(i / max(1, n - 1))
        out.append(f"\x1b[38;2;{r};{g};{b}m{row}\x1b[0m")
    return "\n".join(out)


def colored_lines(variant: str = "compact", fill_top: float = 1.0, fill_bottom: float = 1.0,
                  trim: bool = True) -> list[str]:
    """Colored mark rows, each padded to a uniform width (for placing beside other content)."""
    rows = mark_lines(variant, fill_top, fill_bottom, trim=trim)
    w = max((len(r) for r in rows), default=0)
    rows = [r + _BLANK * (w - len(r)) for r in rows]
    return colorize(rows).split("\n")


@lru_cache(maxsize=None)
def compact_frame(fill_top: float = 1.0, fill_bottom: float = 1.0) -> tuple[str, ...]:
    """The colored `compact` mark at a given sand level, cached. The sand reads as an
    hourglass: it's how far the position has run through its life — top full at open, draining
    to the bottom by expiration. Levels snap to quarters, so the set of (fill_top, fill_bottom)
    pairs is tiny and every frame is rendered exactly once."""
    return tuple(colored_lines("compact", fill_top, fill_bottom, trim=True))


def render_mark(variant: str = "V22", color: bool = True,
                fill_top: float = 1.0, fill_bottom: float = 1.0) -> str:
    rows = mark_lines(variant, fill_top, fill_bottom)
    return colorize(rows) if color else "\n".join(rows)


def main(argv: list[str] | None = None) -> None:
    argv = argv if argv is not None else sys.argv[1:]
    variant = next((a for a in argv if a in _GEOM), "V22")
    plain = "--plain" in argv
    print(render_mark(variant, color=not plain))


if __name__ == "__main__":
    main()
