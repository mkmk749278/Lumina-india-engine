"""LevelBook: swing/round/extra aggregation, clustering, nearest-level queries."""

from __future__ import annotations

from src.level_book import LevelBook
from tests.candle_factory import from_closes

ZIGZAG = from_closes([10.0, 12.0, 11.0, 14.0, 9.0, 13.0])


def test_nearest_support_and_resistance_from_swings() -> None:
    book = LevelBook.build(ZIGZAG, swing_width=1)
    prices = sorted(level.price for level in book.levels())
    assert prices == [8.5, 10.5, 12.5, 14.5]
    assert book.nearest_support(11.0).price == 10.5
    assert book.nearest_resistance(11.0).price == 12.5


def test_no_level_beyond_extremes() -> None:
    book = LevelBook.build(ZIGZAG, swing_width=1)
    assert book.nearest_support(8.0) is None
    assert book.nearest_resistance(15.0) is None


def test_round_levels_generated() -> None:
    # Three bars around 100 with no fractal swings -> only round levels.
    candles = from_closes([98.0, 101.0, 103.0], half_range=1.0)
    book = LevelBook.build(candles, round_step=5.0, swing_width=2)
    round_prices = [level.price for level in book.levels() if level.kind == "round"]
    assert 100.0 in round_prices


def test_clustering_merges_nearby_levels() -> None:
    candles = from_closes([100.0, 100.0, 100.0])  # no swings
    book = LevelBook.build(
        candles,
        extra=[(100.0, "prev_day"), (100.4, "swing_high")],
        merge_tol=0.5,
    )
    levels = book.levels()
    assert len(levels) == 1
    assert levels[0].strength == 2
    assert levels[0].price == 100.2
    # A structural label is preferred over "round" when merging.
    assert levels[0].kind in {"prev_day", "swing_high"}
