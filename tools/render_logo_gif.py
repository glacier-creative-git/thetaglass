"""Render the Thetaglass hourglass as an animated GIF for the README.

The monitor shows the mark as a STATIC, DTE-driven sand level (no animation — it was
distracting in a live TUI). But a single looping GIF in the docs is the right place to
actually *show* the sand moving through its whole life: top-full at open, draining to
bottom-full by expiration, then back, forever.

We render straight from the logo's raw dot bitmap (the 0/1 grid in view/logo.py, before
it's packed into braille) — each dot becomes a glowing circle tinted by the same metallic
bronze→gold gradient — so the result is far crisper than screenshotting braille glyphs.

Requires Pillow (a docs-tooling dep, not a runtime one):  pip install -e '.[gif]'
Usage:  python tools/render_logo_gif.py [out.gif]
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from thetaglass.view import logo

# 11 sand levels — one per 10% of life elapsed. snapped = fraction of life run through;
# top drains as it rises (fill_top = 1 - snapped), bottom fills (fill_bottom = snapped),
# matching how the monitor's health hourglass reads DTE progress.
STEPS = 10
SS = 3                       # supersample factor (downscaled with LANCZOS for smooth dots)
CELL = 9                     # px per bitmap dot, before supersampling
DOT_R = 0.46                 # dot radius as a fraction of a cell
PAD = 3                      # bitmap cells of margin around the union bounding box
BG = (11, 14, 20)            # GitHub-dark-ish card background
GLOW_BLUR = 5                # bloom radius (post-supersample px)


def _grid(snapped: float) -> list[list[int]]:
    """The raw dot bitmap for one sand level (mirrors logo._build, minus the braille pack)."""
    geo = logo._GEOM["compact"]
    g = logo._blank(geo["W"], geo["H"])
    cx, cy = (geo["W"] - 1) / 2, (geo["H"] - 1) / 2
    logo._theta_ring(g, cx, cy, geo["ax"], geo["ay"], geo["base"], geo["amp"])
    ax_in, ay_in = geo["ax"] - geo["idx"], geo["ay"] - geo["idy"]
    logo._hourglass(g, cx, cy, ax_in, ay_in, geo["fy"], geo["neck"], geo["margin"])
    logo._sand(g, cx, cy, ax_in, ay_in, 1.0 - snapped, snapped,
               geo["side_m"], geo["neck_gap"], geo["fy"], geo["neck"], geo["margin"])
    return g


def _union_bbox(grids: list[list[list[int]]]) -> tuple[int, int, int, int]:
    """The tightest box covering every lit dot across ALL frames, so the mark never
    shifts or jitters between frames (only the sand inside it moves)."""
    xs, ys = [], []
    for g in grids:
        for y, row in enumerate(g):
            for x, v in enumerate(row):
                if v:
                    xs.append(x)
                    ys.append(y)
    return min(xs) - PAD, min(ys) - PAD, max(xs) + PAD, max(ys) + PAD


def _render(g, bbox) -> Image.Image:
    """One frame: dots as glowing circles, metallic gradient by vertical position."""
    x0, y0, x1, y1 = bbox
    s = CELL * SS
    w = (x1 - x0 + 1) * s
    h = (y1 - y0 + 1) * s
    span = (y1 - y0) or 1

    dots = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    dd = ImageDraw.Draw(dots)
    r = DOT_R * s
    for y, row in enumerate(g):
        for x, v in enumerate(row):
            if not v:
                continue
            col = logo._metal((y - y0) / span)
            cx = (x - x0) * s + s / 2
            cy = (y - y0) * s + s / 2
            dd.ellipse([cx - r, cy - r, cx + r, cy + r], fill=col + (255,))

    glow = dots.filter(ImageFilter.GaussianBlur(GLOW_BLUR * SS / 2))
    frame = Image.new("RGBA", (w, h), BG + (255,))
    frame.alpha_composite(glow)
    frame.alpha_composite(glow)        # twice → a warmer bloom
    frame.alpha_composite(dots)
    return frame.convert("RGB").resize((w // SS, h // SS), Image.LANCZOS)


def main(out: str = "assets/thetaglass-logo.gif") -> None:
    levels = [i / STEPS for i in range(STEPS + 1)]          # 0.0 … 1.0
    grids = [_grid(s) for s in levels]
    bbox = _union_bbox(grids)
    frames = [_render(g, bbox) for g in grids]

    # ping-pong: drain top→bottom, then refill, for a seamless loop.
    order = list(range(len(frames))) + list(range(len(frames) - 2, 0, -1))
    seq = [frames[i] for i in order]
    # hold the full-top (open) and full-bottom (expiration) ends; flow gently between.
    durations = [1400 if order[k] in (0, len(frames) - 1) else 220 for k in range(len(order))]

    # one shared palette so the gold gradient doesn't flicker frame-to-frame.
    pal = seq[len(frames) - 1].quantize(colors=128, method=Image.MEDIANCUT)
    seq = [f.quantize(palette=pal, dither=Image.NONE) for f in seq]

    Path(out).parent.mkdir(parents=True, exist_ok=True)
    seq[0].save(out, save_all=True, append_images=seq[1:], duration=durations,
                loop=0, disposal=2, optimize=True)
    px = seq[0].size
    print(f"wrote {out}  ({px[0]}×{px[1]}, {len(seq)} frames)")

    # a still of the 50% frame, for quick visual inspection / a static fallback.
    still = out.rsplit(".", 1)[0] + ".still.png"
    frames[STEPS // 2].save(still)
    print(f"wrote {still}")


if __name__ == "__main__":
    main(*(sys.argv[1:3] or ["assets/thetaglass-logo.gif"]))
