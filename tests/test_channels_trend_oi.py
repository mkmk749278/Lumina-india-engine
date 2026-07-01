"""Trend + OI evaluators: TREND_PULLBACK_EMA, OI_SPIKE_REVERSAL."""

from __future__ import annotations

from src.channels.india_scalp import OiSpikeReversal, TrendPullbackEma
from src.indicators import ema
from src.regime import Regime
from src.signals.model import Direction, SetupClass
from tests.candle_factory import c, from_closes
from tests.signal_factory import make_context

# 15m series with a far swing high (24120.5) and swing low (23979.5).
C15 = from_closes([24000.0, 23980.0, 24000.0, 24120.0, 24060.0])


def _trend_pullback_5m() -> list:
    """Uptrend, multi-bar pullback, then a bar that dips below and reclaims the EMA."""
    prices = [23000.0 + i * 10 for i in range(49)] + [23470.0 - j * 14 for j in range(1, 8)]
    base = from_closes(prices)
    closes = [x.close for x in base]
    e21, e55 = ema(closes, 21), ema(closes, 55)
    ref = e21 if abs(closes[-1] - e21) <= abs(closes[-1] - e55) else e55
    reclaim = c(high=ref + 12, low=ref - 6, close=ref + 10)
    return [*base[:-1], reclaim]


def test_trend_pullback_long_emits() -> None:
    ctx = make_context(
        regime_60m=Regime.TRENDING_UP,
        regime_daily=Regime.TRENDING_UP,
        candles_5m=_trend_pullback_5m(),
        candles_15m=C15,
        candles_60m=from_closes([23000.0 + i * 10 for i in range(60)]),
        atr14_5m=100.0,  # volatile session — needed to clear the SL% floor
    )
    sig = TrendPullbackEma().evaluate(ctx)
    assert sig is not None
    assert sig.setup_class == SetupClass.TREND_PULLBACK_EMA
    assert sig.direction == Direction.LONG
    assert sig.sl < sig.entry < sig.tp1
    assert sig.htf_trend_aligned is True  # only fires in an aligned trend


def test_trend_pullback_rejected_when_ranging() -> None:
    ctx = make_context(
        regime_60m=Regime.RANGING,
        candles_5m=_trend_pullback_5m(),
        candles_15m=C15,
        candles_60m=from_closes([23000.0 + i * 10 for i in range(60)]),
        atr14_5m=100.0,
    )
    assert TrendPullbackEma().evaluate(ctx) is None


def _oi_context(**over: object):  # type: ignore[no-untyped-def]
    prev = c(high=23985.0, low=23975.0, close=23980.0)
    pin = c(high=23984.0, low=23940.0, close=23982.0, open_=23978.0)  # bullish pin at support
    defaults = dict(
        regime_60m=Regime.RANGING,
        candles_5m=[prev, pin],
        candles_15m=C15,
        atr14_5m=100.0,
        prev_day_high=24200.0,
        prev_day_low=23900.0,
        prev_day_close=23990.0,
        oi_change_15m_pct=4.0,
        current_oi=10_000_000.0,
    )
    defaults.update(over)
    return make_context(**defaults)  # type: ignore[arg-type]


def test_oi_spike_long_emits() -> None:
    sig = OiSpikeReversal().evaluate(_oi_context())
    assert sig is not None
    assert sig.setup_class == SetupClass.OI_SPIKE_REVERSAL
    assert sig.direction == Direction.LONG
    assert sig.sl < sig.entry < sig.tp1
    assert sig.rr_ratio >= 1.5


def test_oi_spike_rejected_below_spike_threshold() -> None:
    assert OiSpikeReversal().evaluate(_oi_context(oi_change_15m_pct=1.0)) is None


def test_oi_spike_rejected_when_oi_too_small() -> None:
    assert OiSpikeReversal().evaluate(_oi_context(current_oi=1000.0)) is None
