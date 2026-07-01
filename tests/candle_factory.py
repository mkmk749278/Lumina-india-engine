"""Helpers for building Candle fixtures in tests."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

import config
from src.market.candle import Candle

_TS = config.IST.localize(datetime(2026, 7, 8, 10, 0))


def c(high: float, low: float, close: float, volume: float = 1000.0) -> Candle:
    """A candle with an arbitrary (fixed) timestamp; open == close for simplicity."""
    return Candle(ts=_TS, open=close, high=high, low=low, close=close, volume=volume, oi=0.0)


def from_closes(prices: Sequence[float], half_range: float = 0.5) -> list[Candle]:
    """Build candles from close prices, wrapping each in a symmetric H/L band."""
    return [c(high=p + half_range, low=p - half_range, close=p) for p in prices]
