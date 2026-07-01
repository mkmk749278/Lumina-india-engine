"""LIQUIDITY_SWEEP_REVERSAL evaluator (spec §10.1)."""

from __future__ import annotations

from src.channels.india_scalp import LiquiditySweepReversal
from src.market.candle import Candle
from src.regime import Regime
from src.signals.model import Direction, SetupClass
from tests.candle_factory import c, from_closes
from tests.signal_factory import make_context

EVAL = LiquiditySweepReversal()

# 15m series with a swing low at 23979.5 (idx 1) and swing high at 24020.5 (idx 3).
C15 = from_closes([24000.0, 23980.0, 24000.0, 24020.0, 24010.0])


def _c5(current: Candle) -> list[Candle]:
    prior = c(high=23990.0, low=23980.0, close=23985.0, volume=1000.0)
    return [prior, current]


def test_long_sweep_reclaim_emits_signal() -> None:
    sweep = c(high=23990.0, low=23950.0, close=23985.0, volume=2000.0)
    ctx = make_context(
        regime_60m=Regime.RANGING,
        candles_5m=_c5(sweep),
        candles_15m=C15,
        atr14_5m=40.0,
        volume_avg_5m_20=1000.0,
    )
    sig = EVAL.evaluate(ctx)
    assert sig is not None
    assert sig.setup_class == SetupClass.LIQUIDITY_SWEEP_REVERSAL
    assert sig.direction == Direction.LONG
    assert sig.sl < sig.entry < sig.tp1
    assert sig.rr_ratio >= 1.5
    assert sig.htf_trend_aligned is True  # RANGING is aligned for a reversal long
    assert sig.lot_size == 75


def test_short_sweep_reclaim_emits_signal() -> None:
    sweep = c(high=24060.0, low=24010.0, close=24015.0, volume=2000.0)
    ctx = make_context(
        regime_60m=Regime.RANGING,
        candles_5m=_c5(sweep),
        candles_15m=C15,
        atr14_5m=40.0,
        volume_avg_5m_20=1000.0,
    )
    sig = EVAL.evaluate(ctx)
    assert sig is not None
    assert sig.direction == Direction.SHORT
    assert sig.tp1 < sig.entry < sig.sl
    assert sig.rr_ratio >= 1.5


def test_low_volume_sweep_rejected() -> None:
    sweep = c(high=23990.0, low=23950.0, close=23985.0, volume=1000.0)  # == avg, not > 1.2x
    ctx = make_context(
        candles_5m=_c5(sweep), candles_15m=C15, atr14_5m=40.0, volume_avg_5m_20=1000.0
    )
    assert EVAL.evaluate(ctx) is None


def test_no_sweep_no_signal() -> None:
    # Trades inside the swing band: no level swept.
    inside = c(high=24010.0, low=23990.0, close=24000.0, volume=2000.0)
    ctx = make_context(
        candles_5m=_c5(inside), candles_15m=C15, atr14_5m=40.0, volume_avg_5m_20=1000.0
    )
    assert EVAL.evaluate(ctx) is None


def test_downtrend_regime_makes_long_unaligned() -> None:
    sweep = c(high=23990.0, low=23950.0, close=23985.0, volume=2000.0)
    ctx = make_context(
        regime_60m=Regime.TRENDING_DOWN,
        candles_5m=_c5(sweep),
        candles_15m=C15,
        atr14_5m=40.0,
        volume_avg_5m_20=1000.0,
    )
    sig = EVAL.evaluate(ctx)
    assert sig is not None
    assert sig.direction == Direction.LONG
    assert sig.htf_trend_aligned is False  # long against a 60m downtrend
