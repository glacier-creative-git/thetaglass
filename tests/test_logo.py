"""The Thetaglass braille mark (θ + inscribed hourglass)."""
from thetaglass.view.logo import colorize, mark_lines, render_mark

BRAILLE = range(0x2800, 0x28FF + 1)


def _has_braille(s: str) -> bool:
    return any(ord(c) in BRAILLE for c in s)


def test_mark_renders_braille_both_variants():
    for variant in ("V20", "V22"):
        rows = mark_lines(variant)
        assert rows and _has_braille("\n".join(rows))
        # full braille cells (the heavy θ sides) AND lighter ones (thin caps / hourglass)
        joined = "\n".join(rows)
        assert "⣿" in joined                      # solid stroke present (thick θ sides)


def test_v22_is_wider_than_v20():
    w20 = max(len(r) for r in mark_lines("V20"))
    w22 = max(len(r) for r in mark_lines("V22"))
    assert w22 > w20                              # the wider oval is actually wider


def test_colorize_applies_metallic_gradient():
    s = render_mark("V22", color=True)
    assert "\x1b[38;2;" in s                       # truecolor ANSI baked in
    # top is bronze (205,127,50), bottom shifts toward dark goldenrod (184,134,11)
    assert "205;127;50" in s and "184;134;11" in s
    assert render_mark("V22", color=False).count("\x1b") == 0
