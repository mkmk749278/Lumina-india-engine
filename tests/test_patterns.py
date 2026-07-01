"""Candlestick rejection primitives."""

from __future__ import annotations

from src.patterns import (
    is_bearish_engulfing,
    is_bearish_pin_bar,
    is_bullish_engulfing,
    is_bullish_pin_bar,
    is_bullish_rejection,
)
from tests.candle_factory import c


def test_bullish_engulfing() -> None:
    prev = c(high=10.5, low=7.5, close=8.0, open_=10.0)   # bearish
    cur = c(high=11.5, low=6.5, close=11.0, open_=7.0)    # bullish, engulfs
    assert is_bullish_engulfing(prev, cur) is True
    assert is_bearish_engulfing(prev, cur) is False


def test_bearish_engulfing() -> None:
    prev = c(high=11.5, low=8.5, close=11.0, open_=9.0)   # bullish
    cur = c(high=11.5, low=7.5, close=8.0, open_=11.2)    # bearish, engulfs
    assert is_bearish_engulfing(prev, cur) is True


def test_bullish_pin_bar() -> None:
    cur = c(high=100.6, low=99.0, close=100.5, open_=100.4)
    assert is_bullish_pin_bar(cur) is True
    assert is_bearish_pin_bar(cur) is False


def test_bearish_pin_bar() -> None:
    cur = c(high=101.0, low=99.4, close=99.5, open_=99.6)
    assert is_bearish_pin_bar(cur) is True


def test_rejection_pin_needs_no_prior() -> None:
    cur = c(high=100.6, low=99.0, close=100.5, open_=100.4)
    assert is_bullish_rejection(cur) is True


def test_doji_is_not_a_pin() -> None:
    cur = c(high=100.5, low=99.5, close=100.0, open_=100.0)  # zero body
    assert is_bullish_pin_bar(cur) is False
