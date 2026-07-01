"""The OHLCV candle model.

Deliberately a small, typed, immutable value object rather than a pandas
DataFrame: the evaluator pseudocode in the spec indexes ``candle[i].low`` /
``candle[i].close`` directly, which a ``list[Candle]`` models exactly and which
mypy can check. The historical/tick stores (later) materialise these lists per
(symbol, timeframe); the substrate here operates purely on them, no I/O.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Candle:
    """One OHLCV bar. ``ts`` is the bar-open time, IST-aware (CLAUDE.md)."""

    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    oi: float = 0.0


def closes(candles: Sequence[Candle]) -> list[float]:
    return [c.close for c in candles]


def highs(candles: Sequence[Candle]) -> list[float]:
    return [c.high for c in candles]


def lows(candles: Sequence[Candle]) -> list[float]:
    return [c.low for c in candles]


def volumes(candles: Sequence[Candle]) -> list[float]:
    return [c.volume for c in candles]
