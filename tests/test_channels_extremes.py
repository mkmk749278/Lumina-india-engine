"""Reversal/contrarian evaluators: INDIA_VIX_EXTREME, PCR_EXTREME."""

from __future__ import annotations

from src.channels.india_scalp import IndiaVixExtreme, PcrExtreme
from src.regime import Regime
from src.signals.model import Direction, SetupClass
from tests.candle_factory import c, from_closes
from tests.signal_factory import make_context

# 15m series with swing low 23979.5 (idx 1) and a far swing high 24080.5 (idx 3).
C15_PCR = from_closes([24000.0, 23980.0, 24000.0, 24080.0, 24040.0])


def _vix_candles() -> list:
    # 15 declining 5m bars, then a bullish pin-bar reclaim (RSI stays oversold).
    declining = from_closes([24000.0 - 30.0 * i for i in range(15)])
    pin = c(high=23602.0, low=23480.0, close=23600.0, open_=23595.0)
    return [*declining, pin]


def test_vix_extreme_long_emits() -> None:
    ev = IndiaVixExtreme()
    ctx = make_context(
        regime_60m=Regime.RANGING,
        candles_5m=_vix_candles(),
        atr14_5m=30.0,
        india_vix=25.0,
        day_open=24000.0,
        intraday_low=23480.0,
        prev_day_close=24010.0,
    )
    sig = ev.evaluate(ctx)
    assert sig is not None
    assert sig.setup_class == SetupClass.INDIA_VIX_EXTREME
    assert sig.direction == Direction.LONG
    assert sig.sl < sig.entry < sig.tp1
    assert sig.htf_trend_aligned is False  # contrarian by design


def test_vix_extreme_rejected_when_vix_calm() -> None:
    ev = IndiaVixExtreme()
    ctx = make_context(
        candles_5m=_vix_candles(),
        atr14_5m=30.0,
        india_vix=15.0,  # below the extreme threshold
        day_open=24000.0,
        intraday_low=23480.0,
        prev_day_close=24010.0,
    )
    assert ev.evaluate(ctx) is None


def test_pcr_extreme_bearish_gives_contrarian_long() -> None:
    ev = PcrExtreme()
    prev = c(high=23985.0, low=23975.0, close=23980.0)
    pin = c(high=23984.0, low=23940.0, close=23982.0, open_=23978.0)  # bullish pin at support
    ctx = make_context(
        candles_5m=[prev, pin],
        candles_15m=C15_PCR,
        atr14_5m=100.0,
        prev_day_low=23900.0,
        prev_day_close=24010.0,
        oi_change_15m_pct=1.0,
        pcr_is_extreme_bearish=True,
    )
    sig = ev.evaluate(ctx)
    assert sig is not None
    assert sig.setup_class == SetupClass.PCR_EXTREME
    assert sig.direction == Direction.LONG
    assert sig.sl < sig.entry < sig.tp1
    assert sig.rr_ratio >= 1.5


def test_pcr_no_extreme_no_signal() -> None:
    ev = PcrExtreme()
    prev = c(high=23985.0, low=23975.0, close=23980.0)
    pin = c(high=23984.0, low=23940.0, close=23982.0, open_=23978.0)
    ctx = make_context(
        candles_5m=[prev, pin],
        candles_15m=C15_PCR,
        atr14_5m=100.0,
        oi_change_15m_pct=1.0,
        pcr_is_extreme_bearish=False,
        pcr_is_extreme_bullish=False,
    )
    assert ev.evaluate(ctx) is None


def test_pcr_far_from_level_rejected() -> None:
    ev = PcrExtreme()
    prev = c(high=24505.0, low=24495.0, close=24500.0)
    pin = c(high=24504.0, low=24460.0, close=24502.0, open_=24498.0)  # nowhere near support
    ctx = make_context(
        candles_5m=[prev, pin],
        candles_15m=C15_PCR,
        atr14_5m=20.0,
        oi_change_15m_pct=1.0,
        pcr_is_extreme_bearish=True,
    )
    assert ev.evaluate(ctx) is None
