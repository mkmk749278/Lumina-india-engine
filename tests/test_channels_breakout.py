"""Breakout family: OPENING_RANGE_BREAKOUT, VOLUME_SURGE_BREAKOUT, BREAKDOWN_SHORT."""

from __future__ import annotations

from src.channels.india_scalp import (
    BreakdownShort,
    OpeningRangeBreakout,
    VolumeSurgeBreakout,
)
from src.market.candle import Candle
from src.regime import Regime
from src.signals.model import Direction, SetupClass
from tests.candle_factory import c, from_closes
from tests.signal_factory import make_context

# 15m series: swing high 24020.5 (idx 3), swing low 23979.5 (idx 1).
C15 = from_closes([24000.0, 23980.0, 24000.0, 24020.0, 24010.0])


def _c5(current: Candle) -> list[Candle]:
    return [c(high=24000.0, low=23995.0, close=23998.0, volume=1000.0), current]


# ---- OPENING_RANGE_BREAKOUT ----

def test_orb_long_breakout() -> None:
    orb = OpeningRangeBreakout()
    current = c(high=24070.0, low=24050.0, close=24060.0, volume=2000.0)
    ctx = make_context(
        regime_60m=Regime.TRENDING_UP,
        candles_5m=[current],
        atr14_5m=20.0,
        volume_avg_5m_20=1000.0,
        opening_range_high=24050.0,
        opening_range_low=23950.0,
    )
    sig = orb.evaluate(ctx)
    assert sig is not None
    assert sig.setup_class == SetupClass.OPENING_RANGE_BREAKOUT
    assert sig.direction == Direction.LONG
    assert sig.sl < sig.entry < sig.tp1
    assert sig.breakout_volume_ratio == 2.0
    assert sig.htf_trend_aligned is True


def test_orb_stale_opening_range_after_window_rejected() -> None:
    from datetime import time as _time

    orb = OpeningRangeBreakout()
    current = c(high=24070.0, low=24050.0, close=24060.0, volume=2000.0)

    def _ctx(scan_time: _time | None):
        return make_context(
            regime_60m=Regime.TRENDING_UP,
            candles_5m=[current],
            atr14_5m=20.0,
            volume_avg_5m_20=1000.0,
            opening_range_high=24050.0,
            opening_range_low=23950.0,
            scan_time_ist=scan_time,
        )

    # Same setup fires inside the window, is suppressed as a stale-range trade
    # once past ORB_WINDOW_END (the 12:22 BHARTIARTL ORB that prompted this).
    assert orb.evaluate(_ctx(_time(10, 0))) is not None
    assert orb.evaluate(_ctx(_time(12, 22))) is None


def test_orb_range_too_tight_rejected() -> None:
    orb = OpeningRangeBreakout()
    current = c(high=24010.0, low=23990.0, close=24005.0, volume=3000.0)
    ctx = make_context(
        candles_5m=[current],
        atr14_5m=20.0,
        opening_range_high=24000.0,
        opening_range_low=23999.0,  # 1-point range -> below ORB_MIN_RANGE_PCT
    )
    assert orb.evaluate(ctx) is None


# ---- VOLUME_SURGE_BREAKOUT ----

def test_vsb_long_breakout() -> None:
    vsb = VolumeSurgeBreakout()
    # Wide bullish bar: opens below the swing high, closes well above it.
    current = c(high=24040.0, low=23975.0, close=24035.0, volume=2500.0, open_=23980.0)
    ctx = make_context(
        regime_60m=Regime.TRENDING_UP,
        candles_5m=_c5(current),
        candles_15m=C15,
        atr14_5m=20.0,
        volume_avg_5m_20=1000.0,
        oi_change_15m_pct=1.0,
    )
    sig = vsb.evaluate(ctx)
    assert sig is not None
    assert sig.direction == Direction.LONG
    assert sig.setup_class == SetupClass.VOLUME_SURGE_BREAKOUT
    assert sig.sl < sig.entry < sig.tp1
    assert sig.breakout_volume_ratio == 2.5


def test_vsb_rejected_without_oi_confirmation() -> None:
    vsb = VolumeSurgeBreakout()
    current = c(high=24040.0, low=23975.0, close=24035.0, volume=2500.0, open_=23980.0)
    ctx = make_context(
        candles_5m=_c5(current),
        candles_15m=C15,
        atr14_5m=20.0,
        volume_avg_5m_20=1000.0,
        oi_change_15m_pct=-0.5,  # OI unwinding (< VSB_OI_MIN_PCT 0.0) -> rejected
    )
    assert vsb.evaluate(ctx) is None


def test_vsb_rejected_without_volume_surge() -> None:
    vsb = VolumeSurgeBreakout()
    current = c(high=24040.0, low=23975.0, close=24035.0, volume=1200.0, open_=23980.0)
    ctx = make_context(
        candles_5m=_c5(current),
        candles_15m=C15,
        atr14_5m=20.0,
        volume_avg_5m_20=1000.0,  # ratio 1.2 < VSB_VOLUME_MULT (2.0)
        oi_change_15m_pct=1.0,
    )
    assert vsb.evaluate(ctx) is None


# ---- BREAKDOWN_SHORT ----

def test_bds_short_breakdown() -> None:
    bds = BreakdownShort()
    current = c(high=24025.0, low=23960.0, close=23965.0, volume=2500.0, open_=24020.0)
    ctx = make_context(
        regime_60m=Regime.TRENDING_DOWN,
        candles_5m=_c5(current),
        candles_15m=C15,
        atr14_5m=20.0,
        volume_avg_5m_20=1000.0,
        oi_change_15m_pct=1.0,
    )
    sig = bds.evaluate(ctx)
    assert sig is not None
    assert sig.direction == Direction.SHORT
    assert sig.setup_class == SetupClass.BREAKDOWN_SHORT
    assert sig.tp1 < sig.entry < sig.sl
    assert sig.htf_trend_aligned is True
