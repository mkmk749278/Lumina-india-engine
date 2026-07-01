"""Indicator helpers: EMA, RSI (Wilder), ATR (Wilder), Bollinger, VWAP, rolling mean."""

from __future__ import annotations

import pytest

from src import indicators as ind
from tests.candle_factory import c, from_closes


def test_ema_of_constant_is_constant() -> None:
    assert ind.ema([5.0] * 10, period=3) == pytest.approx(5.0)


def test_ema_period_one_tracks_last_value() -> None:
    # alpha == 1 when period == 1, so EMA equals the latest sample.
    assert ind.ema([1.0, 2.0, 3.0, 9.0], period=1) == pytest.approx(9.0)


def test_ema_lags_below_a_rising_series() -> None:
    values = [float(i) for i in range(1, 21)]
    assert ind.ema(values, period=5) < values[-1]


def test_rsi_strictly_rising_is_100() -> None:
    assert ind.rsi([float(i) for i in range(1, 20)], period=14) == pytest.approx(100.0)


def test_rsi_flat_is_50() -> None:
    assert ind.rsi([7.0] * 20, period=14) == pytest.approx(50.0)


def test_rsi_requires_enough_samples() -> None:
    with pytest.raises(ValueError, match="need >="):
        ind.rsi([1.0, 2.0, 3.0], period=14)


def test_atr_constant_range() -> None:
    # high-low == 2 every bar, close flat -> true range 2 -> ATR 2.
    candles = [c(high=10.0, low=8.0, close=9.0) for _ in range(20)]
    assert ind.atr(candles, period=14) == pytest.approx(2.0)


def test_bollinger_zero_width_on_constant() -> None:
    upper, mid, lower = ind.bollinger([4.0] * 25, period=20)
    assert upper == pytest.approx(4.0)
    assert mid == pytest.approx(4.0)
    assert lower == pytest.approx(4.0)


def test_vwap_of_constant_price() -> None:
    candles = from_closes([100.0, 100.0, 100.0])
    assert ind.vwap(candles) == pytest.approx(100.0)


def test_vwap_zero_volume_raises() -> None:
    candles = [c(high=1.0, low=1.0, close=1.0, volume=0.0)]
    with pytest.raises(ValueError, match="zero"):
        ind.vwap(candles)


def test_rolling_mean() -> None:
    assert ind.rolling_mean([1.0, 2.0, 3.0, 4.0], period=2) == pytest.approx(3.5)
