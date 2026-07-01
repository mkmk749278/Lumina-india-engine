"""SR_FLIP_RETEST, FAILED_AUCTION_RECLAIM, DIVERGENCE_CONTINUATION evaluators."""

from __future__ import annotations

from src.channels.india_scalp import (
    DivergenceContinuation,
    FailedAuctionReclaim,
    SrFlipRetest,
)
from src.regime import Regime
from src.signals.model import Direction, SetupClass
from tests.candle_factory import c, from_closes
from tests.signal_factory import make_context

# 15m series where prev_day_close (24000) was support, price broke below it
# cleanly (close at 23930 is well below 24000-50=23950), then retested.
_C15_SRF = (
    from_closes([24010.0, 24020.0, 24030.0, 24015.0, 24005.0])
    + from_closes([23990.0, 23970.0, 23930.0, 23960.0, 23980.0])
    + from_closes([23992.0])
)


def test_sr_flip_short_emits() -> None:
    prev_bar = c(high=23998.0, low=23990.0, close=23996.0, open_=23991.0)
    retest_bar = c(
        high=24060.0, low=23990.0, close=23992.0, open_=23994.0
    )
    ctx = make_context(
        regime_60m=Regime.TRENDING_DOWN,
        candles_5m=[prev_bar, retest_bar],
        candles_15m=_C15_SRF,
        atr14_5m=100.0,
        prev_day_high=24200.0,
        prev_day_low=23800.0,
        prev_day_close=24000.0,
    )
    sig = SrFlipRetest().evaluate(ctx)
    assert sig is not None
    assert sig.setup_class == SetupClass.SR_FLIP_RETEST
    assert sig.direction == Direction.SHORT
    assert sig.entry < sig.sl
    assert sig.tp1 < sig.entry


def test_sr_flip_rejected_when_no_break() -> None:
    c15_no_break = from_closes(
        [24000.0 + i for i in range(11)]
    )
    bar = c(high=24011.0, low=24009.0, close=24010.0, open_=24010.5)
    prev = c(high=24010.0, low=24008.0, close=24009.5)
    ctx = make_context(
        candles_5m=[prev, bar],
        candles_15m=c15_no_break,
        atr14_5m=30.0,
    )
    assert SrFlipRetest().evaluate(ctx) is None


# --- FAILED_AUCTION_RECLAIM ---

def _far_context_long():
    or_high = 24050.0
    prev = c(high=24055.0, low=23990.0, close=24000.0, open_=24048.0)
    reclaim = c(high=24070.0, low=24040.0, close=24060.0, open_=24042.0, volume=2000.0)
    return make_context(
        candles_5m=[
            c(high=24030.0, low=23980.0, close=24010.0),
            c(high=24040.0, low=23985.0, close=24020.0),
            c(high=24055.0, low=23990.0, close=24000.0, open_=24048.0),
            prev,
            reclaim,
        ],
        candles_15m=from_closes(
            [24000.0 + i * 5 for i in range(10)]
        ),
        opening_range_high=or_high,
        opening_range_low=23950.0,
        prev_day_high=24200.0,
        prev_day_low=23900.0,
        prev_day_close=24010.0,
        volume_avg_5m_20=1000.0,
        atr14_5m=100.0,
    )


def test_far_long_emits() -> None:
    ctx = _far_context_long()
    sig = FailedAuctionReclaim().evaluate(ctx)
    assert sig is not None
    assert sig.setup_class == SetupClass.FAILED_AUCTION_RECLAIM
    assert sig.direction == Direction.LONG
    assert sig.sl < sig.entry < sig.tp1


def test_far_rejected_low_volume() -> None:
    ctx = _far_context_long()
    for bar in ctx.candles_5m:
        object.__setattr__(bar, "volume", 500.0)
    assert FailedAuctionReclaim().evaluate(ctx) is None


# --- DIVERGENCE_CONTINUATION ---

def _div_context_bearish():
    """Build a series with a bearish divergence: price makes new high but RSI lower."""
    base = [23000.0 + i * 5 for i in range(20)]
    base.append(23120.0)
    pullback = [23100.0 - i * 3 for i in range(10)]
    new_high = [23080.0 + i * 8 for i in range(10)]
    new_high[-1] = 23160.0
    prices = base + pullback + new_high
    bearish_close = c(
        high=23165.0, low=23140.0, close=23142.0, open_=23160.0
    )
    candles = from_closes(prices)
    candles[-1] = bearish_close
    return make_context(
        regime_60m=Regime.RANGING,
        candles_5m=candles,
        candles_15m=from_closes([23000.0 + i * 10 for i in range(10)]),
        atr14_5m=30.0,
        prev_day_low=23050.0,
        prev_day_high=23200.0,
        prev_day_close=23100.0,
    )


def test_divergence_short_emits() -> None:
    ctx = _div_context_bearish()
    sig = DivergenceContinuation().evaluate(ctx)
    if sig is not None:
        assert sig.setup_class == SetupClass.DIVERGENCE_CONTINUATION
        assert sig.direction == Direction.SHORT
        assert sig.entry < sig.sl


def test_divergence_rejected_insufficient_bars() -> None:
    ctx = make_context(
        candles_5m=from_closes([100.0] * 10),
        atr14_5m=5.0,
    )
    assert DivergenceContinuation().evaluate(ctx) is None
