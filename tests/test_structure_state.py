"""Market structure: swing detection, last swing levels, BOS / CHoCH."""

from __future__ import annotations

from src.structure_state import (
    StructureEvent,
    detect_structure,
    find_swings,
    last_structure_event,
    last_swing_high,
    last_swing_low,
)
from tests.candle_factory import from_closes

# from_closes wraps each close in a +/-0.5 band, so highs/lows track closes.
ZIGZAG = from_closes([10.0, 12.0, 11.0, 14.0, 9.0, 13.0])


def test_find_swings_identifies_fractals() -> None:
    swings = find_swings(ZIGZAG, width=1)
    highs = [(s.index, s.price) for s in swings if s.is_high]
    lows = [(s.index, s.price) for s in swings if not s.is_high]
    assert highs == [(1, 12.5), (3, 14.5)]
    assert lows == [(2, 10.5), (4, 8.5)]


def test_last_swing_levels() -> None:
    assert last_swing_high(ZIGZAG, lookback=20, width=1) == 14.5
    assert last_swing_low(ZIGZAG, lookback=20, width=1) == 8.5


def test_last_swing_high_none_when_no_pivot() -> None:
    assert last_swing_high(from_closes([1.0, 2.0, 3.0, 4.0]), width=1) is None


def test_bos_up_on_continuation_break() -> None:
    # Higher highs + higher lows, final close breaks the last swing high.
    candles = from_closes([10.0, 8.0, 12.0, 9.0, 15.0, 11.0, 20.0])
    assert detect_structure(candles, width=1) is StructureEvent.BOS_UP


def test_choch_down_on_reversal_break() -> None:
    # Same uptrend structure, but the final close breaks the last swing low.
    candles = from_closes([10.0, 8.0, 12.0, 9.0, 15.0, 11.0, 7.0])
    assert detect_structure(candles, width=1) is StructureEvent.CHOCH_DOWN


def test_no_structure_event_inside_range() -> None:
    assert detect_structure(ZIGZAG, width=1) is None


def test_equal_highs_register_one_swing() -> None:
    # An exact double-top (routine at NSE round numbers) must still register
    # a swing at its first bar — a fully strict fractal saw nothing here.
    candles = from_closes([10.0, 14.0, 14.0, 10.0, 11.0])
    swings = find_swings(candles, width=1)
    highs = [(s.index, s.price) for s in swings if s.is_high]
    assert highs == [(1, 14.5)]


def test_last_structure_event_persists_after_break_bar() -> None:
    # BOS_UP printed two bars ago; price has drifted since without breaking
    # anything else. The persistent read still reports BOS_UP — the one-shot
    # detect_structure on the newest close alone would return None.
    candles = from_closes([10.0, 8.0, 12.0, 9.0, 15.0, 11.0, 20.0, 19.0, 18.0])
    assert last_structure_event(candles, width=1, lookback=8) is StructureEvent.BOS_UP


def test_last_structure_event_none_outside_lookback() -> None:
    quiet_tail = [13.0, 13.2, 13.1, 13.2, 13.1, 13.2, 13.1, 13.2, 13.1, 13.2]
    candles = from_closes([10.0, 8.0, 12.0, 9.0, 15.0, 11.0, 20.0, *quiet_tail])
    assert last_structure_event(candles, width=1, lookback=3) is None
