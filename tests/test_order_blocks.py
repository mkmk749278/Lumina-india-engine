"""Order blocks and FVGs: detection + mitigation filtering."""

from __future__ import annotations

from src.order_blocks import find_fvgs, find_order_blocks, unmitigated
from tests.candle_factory import c


def test_bullish_fvg_detected() -> None:
    candles = [
        c(high=10.0, low=9.0, close=9.5),   # prev
        c(high=12.0, low=11.0, close=11.5),  # middle (i=1)
        c(high=13.0, low=11.0, close=12.0),  # next: low 11 > prev high 10 -> gap
    ]
    fvgs = find_fvgs(candles)
    assert len(fvgs) == 1
    z = fvgs[0]
    assert z.bullish is True
    assert (z.bottom, z.top) == (10.0, 11.0)


def test_bearish_fvg_detected() -> None:
    candles = [
        c(high=13.0, low=12.0, close=12.5),
        c(high=11.0, low=10.0, close=10.5),
        c(high=11.0, low=9.0, close=9.5),  # next high 11 < prev low 12 -> gap
    ]
    fvgs = find_fvgs(candles)
    assert len(fvgs) == 1
    assert fvgs[0].bullish is False


def test_bullish_order_block() -> None:
    candles = [
        c(high=10.2, low=7.8, close=8.0, open_=10.0),   # down candle
        c(high=11.2, low=7.9, close=11.0, open_=8.0),   # up, closes above prev high
    ]
    obs = find_order_blocks(candles)
    assert len(obs) == 1
    assert obs[0].bullish is True
    assert (obs[0].bottom, obs[0].top) == (7.8, 10.2)


def test_bearish_order_block() -> None:
    candles = [
        c(high=10.2, low=7.9, close=10.0, open_=8.0),   # up candle
        c(high=10.1, low=6.8, close=7.0, open_=10.0),   # down, closes below prev low
    ]
    obs = find_order_blocks(candles)
    assert len(obs) == 1
    assert obs[0].bullish is False


def test_unmitigated_filter() -> None:
    candles = [
        c(high=10.0, low=9.0, close=9.5),
        c(high=12.0, low=11.0, close=11.5),
        c(high=13.0, low=11.0, close=12.0),   # bullish FVG [10, 11] at i=1
        c(high=13.5, low=10.5, close=11.0),   # trades back into the gap -> mitigated
    ]
    fvgs = find_fvgs(candles)
    assert len(fvgs) == 1
    assert unmitigated(fvgs, candles) == []
