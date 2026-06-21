"""The Thetaglass mark — a Greek θ with a curvy-X hourglass inscribed inside it.

Rendered in braille (U+2800–U+28FF), the same technique the Hermes agent CLI uses for its
caduceus: a high-resolution dot bitmap (2×4 dots per character cell) packed into braille
codepoints, then tinted with a metallic bronze→gold→bronze vertical gradient.

The θ carries its weight on the SIDES (thin top/bottom), matching the real glyph. The
hourglass is two concave walls that hug the oval near the top/bottom and sweep into a
pinched neck — a "curvy X" — with its prongs attached to the oval ring so it always fits.

  render_mark(variant) → the colored mark as a multi-line ANSI string ("V20" | "V22")
  mark_lines(variant)  → the raw braille rows (no color)
  python -m thetaglass.view.logo [V20|V22] [--plain]   # cat it in the terminal

The geometry is generated (not hand-drawn), so every dimension below is a tunable knob.
"""
from __future__ import annotations

import math
import sys

# canvas + shape constants (px space is 2×4 the braille-cell grid)
_W, _H = 54, 68
_AY = 29                       # oval vertical radius
_THETA_BASE = 0.7              # min ring thickness (the thin top/bottom caps)
_THETA_AMP = 1.4              # extra thickness added on the sides (the θ's stress)
_HG_FY = 0.74                  # how high up the oval the hourglass prongs attach (0..1)
_HG_NECK = 2.5                 # half-width of the pinched neck, px
_HG_MARGIN = 1.0               # gap between the hourglass prongs and the oval ring, px
_VARIANTS = {"V20": 20, "V22": 22}   # oval horizontal radius per width variant

# metallic gradient stops (vertical fraction → rgb), per the Hermes palette
_STOPS = [(0.0, (205, 127, 50)), (0.28, (255, 191, 0)), (0.5, (255, 215, 0)),
          (0.72, (255, 191, 0)), (0.88, (205, 127, 50)), (1.0, (184, 134, 11))]

_DOT = [[0x01, 0x08], [0x02, 0x10], [0x04, 0x20], [0x40, 0x80]]   # [dy][dx] → bit


def _blank(w: int, h: int) -> list[list[int]]:
    return [[0] * w for _ in range(h)]


def _px(g: list[list[int]], x: float, y: float) -> None:
    xi, yi = int(round(x)), int(round(y))
    if 0 <= yi < len(g) and 0 <= xi < len(g[0]):
        g[yi][xi] = 1


def _to_braille(g: list[list[int]]) -> list[str]:
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
        out.append(line.rstrip("⠀") or "⠀")
    return out


def _theta_ring(g, cx, cy, ax, ay, base=_THETA_BASE, amp=_THETA_AMP) -> None:
    """An oval ring whose stroke is thick on the sides, thin at top/bottom (real θ stress)."""
    for y in range(len(g)):
        for x in range(len(g[0])):
            nx = (x - cx) / ax
            ny = (y - cy) / ay
            e = nx * nx + ny * ny
            gm = 2 * math.hypot(nx / ax, ny / ay) or 1e-9
            dist = abs(e - 1) / gm
            cos = abs(nx) / math.sqrt(e + 1e-9)        # ~|cos|: 1 at sides, 0 at top/bottom
            if dist < base + amp * cos * cos and e < 1.3:
                g[y][x] = 1


def _hourglass(g, cx, cy, ax_in, ay_in, fy=_HG_FY, neck=_HG_NECK, margin=_HG_MARGIN) -> None:
    """Two concave walls (sine profile) from the oval ring down into a pinched neck — the
    curvy-X hourglass. Prongs attach at height `fy` of the inner oval, so it always fits."""
    top_y = cy - fy * ay_in
    bot_y = cy + fy * ay_in
    w_end = ax_in * math.sqrt(1 - fy * fy) - margin
    half = fy * ay_in
    steps = int((bot_y - top_y) * 6)
    for i in range(steps + 1):
        y = top_y + (bot_y - top_y) * i / steps
        v = (y - cy) / half
        hw = neck + (w_end - neck) * math.sin(min(1.0, abs(v)) * math.pi / 2)
        _px(g, cx - hw, y)
        _px(g, cx + hw, y)


def mark_lines(variant: str = "V22") -> list[str]:
    """The mark as raw braille rows (no color)."""
    ax = _VARIANTS.get(variant, _VARIANTS["V22"])
    g = _blank(_W, _H)
    cx, cy = (_W - 1) / 2, (_H - 1) / 2
    _theta_ring(g, cx, cy, ax, _AY)
    _hourglass(g, cx, cy, ax - 2.5, _AY - 1.5)
    return _to_braille(g)


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


def render_mark(variant: str = "V22", color: bool = True) -> str:
    rows = mark_lines(variant)
    return colorize(rows) if color else "\n".join(rows)


def main(argv: list[str] | None = None) -> None:
    argv = argv if argv is not None else sys.argv[1:]
    variant = next((a for a in argv if a in _VARIANTS), "V22")
    plain = "--plain" in argv
    print(render_mark(variant, color=not plain))


if __name__ == "__main__":
    main()
