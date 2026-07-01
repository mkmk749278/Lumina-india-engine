"""QUIET_COMPRESSION_BREAK, MA_CROSS_TREND_SHIFT, EXPIRY_GAMMA_SQUEEZE evaluators."""

from __future__ import annotations

from datetime import time

from src.channels.india_scalp import (
    ExpiryGammaSqueeze,
    MaCrossTrendShift,
    QuietCompressionBreak,
)
from src.indicators import ema_series
from src.regime import Regime
from src.signals.model import Direction, SetupClass
from tests.candle_factory import c, from_closes
from tests.signal_factory import make_context

# --- QUIET_COMPRESSION_BREAK ---

def _qcb_squeeze_candles():
    """Build a series with a tight Bollinger squeeze then a breakout bar."""
    flat = [24000.0] * 30
    flat[-1] = 24000.0 + 50
    candles = from_closes(flat, half_range=0.1)
    breakout = c(
        high=24060.0, low=24040.0, close=24055.0, open_=24001.0, volume=2000.0
    )
    candles[-1] = breakout
    return candles


def test_qcb_long_emits() -> None:
    ctx = make_context(
        candles_5m=_qcb_squeeze_candles(),
        candles_15m=from_closes([24000.0 + i for i in range(10)]),
        volume_avg_5m_20=1000.0,
        atr14_5m=20.0,
        scan_time_ist=time(11, 30),
    )
    sig = QuietCompressionBreak().evaluate(ctx)
    assert sig is not None
    assert sig.setup_class == SetupClass.QUIET_COMPRESSION_BREAK
    assert sig.direction == Direction.LONG
    assert sig.sl < sig.entry


def test_qcb_rejected_outside_time_window() -> None:
    ctx = make_context(
        candles_5m=_qcb_squeeze_candles(),
        candles_15m=from_closes([24000.0 + i for i in range(10)]),
        volume_avg_5m_20=1000.0,
        atr14_5m=20.0,
        scan_time_ist=time(9, 20),
    )
    assert QuietCompressionBreak().evaluate(ctx) is None


def test_qcb_rejected_low_volume() -> None:
    candles = _qcb_squeeze_candles()
    candles[-1] = c(
        high=24060.0, low=24040.0, close=24055.0, open_=24001.0, volume=500.0
    )
    ctx = make_context(
        candles_5m=candles,
        candles_15m=from_closes([24000.0 + i for i in range(10)]),
        volume_avg_5m_20=1000.0,
        atr14_5m=20.0,
        scan_time_ist=time(11, 30),
    )
    assert QuietCompressionBreak().evaluate(ctx) is None


# --- MA_CROSS_TREND_SHIFT ---

def _mac_context_long():
    """Build 15m candles where EMA21 crosses above EMA55 on the last bar."""
    prices = [23000.0 + i * 2 for i in range(40)]
    prices += [23050.0 - i * 3 for i in range(15)]
    prices += [22990.0 + i * 6 for i in range(15)]
    candles = from_closes(prices)
    last = candles[-1]
    candles[-1] = c(
        high=last.high, low=last.low, close=last.close,
        open_=last.close - 2, volume=2000.0,
    )
    return candles


def test_mac_long_emits() -> None:
    c15 = _mac_context_long()
    closes = [bar.close for bar in c15]
    e21 = ema_series(closes, 21)
    e55 = ema_series(closes, 55)
    if e21[-1] <= e55[-1] or e21[-2] >= e55[-2]:
        return
    ctx = make_context(
        regime_60m=Regime.TRENDING_UP,
        candles_5m=from_closes([23050.0 + i for i in range(20)]),
        candles_15m=c15,
        atr14_5m=30.0,
        volume_avg_15m_20=1000.0,
        prev_day_high=23200.0,
        prev_day_low=22900.0,
        prev_day_close=23050.0,
    )
    sig = MaCrossTrendShift().evaluate(ctx)
    assert sig is not None
    assert sig.setup_class == SetupClass.MA_CROSS_TREND_SHIFT
    assert sig.direction == Direction.LONG


def test_mac_rejected_htf_opposing() -> None:
    c15 = _mac_context_long()
    closes = [bar.close for bar in c15]
    e21 = ema_series(closes, 21)
    e55 = ema_series(closes, 55)
    if e21[-1] <= e55[-1] or e21[-2] >= e55[-2]:
        return
    ctx = make_context(
        regime_60m=Regime.TRENDING_DOWN,
        candles_5m=from_closes([23050.0 + i for i in range(20)]),
        candles_15m=c15,
        atr14_5m=30.0,
        volume_avg_15m_20=1000.0,
    )
    assert MaCrossTrendShift().evaluate(ctx) is None


def test_mac_rejected_no_cross() -> None:
    c15 = from_closes([24000.0 + i * 2 for i in range(60)])
    ctx = make_context(
        regime_60m=Regime.TRENDING_UP,
        candles_5m=from_closes([24000.0] * 20),
        candles_15m=c15,
        atr14_5m=30.0,
        volume_avg_15m_20=1000.0,
    )
    assert MaCrossTrendShift().evaluate(ctx) is None


# --- EXPIRY_GAMMA_SQUEEZE ---

def test_egs_long_emits() -> None:
    ctx = make_context(
        candles_5m=[c(high=24010.0, low=23990.0, close=24000.0)],
        atr14_5m=60.0,
        is_expiry_day=True,
        scan_time_ist=time(13, 30),
        max_pain_strike=24100.0,
    )
    sig = ExpiryGammaSqueeze().evaluate(ctx)
    assert sig is not None
    assert sig.setup_class == SetupClass.EXPIRY_GAMMA_SQUEEZE
    assert sig.direction == Direction.LONG
    assert sig.tp1 == 24100.0
    assert sig.sl < sig.entry


def test_egs_short_emits() -> None:
    ctx = make_context(
        candles_5m=[c(high=24010.0, low=23990.0, close=24000.0)],
        atr14_5m=60.0,
        is_expiry_day=True,
        scan_time_ist=time(14, 0),
        max_pain_strike=23900.0,
    )
    sig = ExpiryGammaSqueeze().evaluate(ctx)
    assert sig is not None
    assert sig.direction == Direction.SHORT
    assert sig.tp1 == 23900.0


def test_egs_rejected_not_expiry() -> None:
    ctx = make_context(
        candles_5m=[c(high=24010.0, low=23990.0, close=24000.0)],
        atr14_5m=40.0,
        is_expiry_day=False,
        scan_time_ist=time(13, 30),
        max_pain_strike=24100.0,
    )
    assert ExpiryGammaSqueeze().evaluate(ctx) is None


def test_egs_rejected_outside_window() -> None:
    ctx = make_context(
        candles_5m=[c(high=24010.0, low=23990.0, close=24000.0)],
        atr14_5m=40.0,
        is_expiry_day=True,
        scan_time_ist=time(10, 0),
        max_pain_strike=24100.0,
    )
    assert ExpiryGammaSqueeze().evaluate(ctx) is None


def test_egs_rejected_too_close_to_max_pain() -> None:
    ctx = make_context(
        candles_5m=[c(high=24010.0, low=23990.0, close=24000.0)],
        atr14_5m=40.0,
        is_expiry_day=True,
        scan_time_ist=time(13, 30),
        max_pain_strike=24010.0,
    )
    assert ExpiryGammaSqueeze().evaluate(ctx) is None


def test_egs_rejected_too_far_from_max_pain() -> None:
    ctx = make_context(
        candles_5m=[c(high=24010.0, low=23990.0, close=24000.0)],
        atr14_5m=40.0,
        is_expiry_day=True,
        scan_time_ist=time(13, 30),
        max_pain_strike=25000.0,
    )
    assert ExpiryGammaSqueeze().evaluate(ctx) is None
