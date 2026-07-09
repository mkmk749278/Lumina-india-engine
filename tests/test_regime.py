"""Regime classification: TRENDING_UP/DOWN, QUIET, and conservative defaults."""

from __future__ import annotations

from src.regime import Regime, classify
from tests.candle_factory import from_closes


def test_insufficient_history_is_ranging() -> None:
    assert classify(from_closes([100.0] * 10)) is Regime.RANGING


def test_flat_low_volatility_is_quiet() -> None:
    # Truly flat bars (no H/L band) -> ATR 0 -> below the QUIET ATR% floor.
    assert classify(from_closes([100.0] * 80, half_range=0.0)) is Regime.QUIET


def test_strong_uptrend() -> None:
    prices = [100.0 + i for i in range(80)]
    assert classify(from_closes(prices)) is Regime.TRENDING_UP


def test_strong_downtrend() -> None:
    prices = [300.0 - i for i in range(80)]
    assert classify(from_closes(prices)) is Regime.TRENDING_DOWN


def test_ordered_but_flat_ema_stack_is_ranging() -> None:
    # A barely-drifting series keeps EMA21 > EMA55 but with separation far
    # below REGIME_MIN_EMA_SEP_ATR x ATR — that is chop, not a trend.
    prices = [100.0 + i * 0.002 for i in range(80)]
    assert classify(from_closes(prices)) is Regime.RANGING
