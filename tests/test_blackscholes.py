"""Black-Scholes pricing + implied-vol inversion (entry-IV reconstruction)."""
from thetaglass.state.blackscholes import (bs_price, implied_vol, position_entry_iv)


def test_price_then_invert_recovers_vol():
    # price an option at a known vol, then invert the price → recover that vol
    for opt, S, K in [("put", 740, 729), ("call", 100, 105), ("put", 50, 50)]:
        true = 0.27
        px = bs_price(opt, S, K, 30 / 365, 0.04, true)
        got = implied_vol(opt, px, S, K, 30 / 365, 0.04)
        assert got is not None and abs(got - true) < 1e-3


def test_invert_rejects_impossible_price():
    # a price below intrinsic (here, way under) isn't invertible
    assert implied_vol("put", 0.01, 740, 800, 30 / 365) is None   # intrinsic ≈ 60
    assert implied_vol("call", 0.0, 100, 100, 30 / 365) is None


def test_position_entry_iv_from_fill():
    # build a short put whose fill corresponds to a known IV at the open-day underlying
    S_open, K, dte, true = 733.0, 729.0, 30, 0.26
    fill_per_share = bs_price("put", S_open, K, dte / 365, 0.04, true)
    pos = {
        "opened_at": "2026-06-17T15:06:36Z", "dte_at_open": dte, "underlying": "QQQ",
        "legs": [{"side": "short", "option_type": "put", "strike": K,
                  "average_price": -fill_per_share * 100}],   # per-contract $, credit sign
    }
    closes = [("2026-06-16", 730.0), ("2026-06-17", S_open), ("2026-06-18", 740.0)]
    iv = position_entry_iv(pos, closes)
    assert iv is not None and abs(iv - true) < 5e-3
