"""Technical-indicator helpers (pure functions, no external deps).

Shared read-only math the evaluators consume. Each evaluator still owns its
SL/TP geometry (CLAUDE.md) — these only supply the raw indicator values
(EMA/RSI/ATR/Bollinger/VWAP). Implemented directly rather than via ``ta`` so
the formulas are explicit, dependency-free, and unit-testable.

RSI and ATR use Wilder's smoothing (the standard for 14-period defaults).
Functions raise ``ValueError`` on insufficient input rather than returning a
silent sentinel — a caller must supply enough bars.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence

from src.market.candle import Candle


def ema_series(values: Sequence[float], period: int) -> list[float]:
    """Exponential moving average across ``values`` (seeded with the first value)."""
    if not values:
        raise ValueError("ema_series: values is empty")
    if period < 1:
        raise ValueError("ema_series: period must be >= 1")
    alpha = 2.0 / (period + 1)
    out = [float(values[0])]
    for v in values[1:]:
        out.append(alpha * float(v) + (1.0 - alpha) * out[-1])
    return out


def ema(values: Sequence[float], period: int) -> float:
    """Latest EMA value."""
    return ema_series(values, period)[-1]


def rsi(values: Sequence[float], period: int = 14) -> float:
    """Wilder's RSI (0–100) of the latest bar."""
    if len(values) < period + 1:
        raise ValueError(f"rsi: need >= {period + 1} values, got {len(values)}")
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, len(values)):
        delta = float(values[i]) - float(values[i - 1])
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0.0:
        return 100.0 if avg_gain > 0.0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def true_ranges(candles: Sequence[Candle]) -> list[float]:
    """True range for each bar after the first."""
    trs: list[float] = []
    for i in range(1, len(candles)):
        high = candles[i].high
        low = candles[i].low
        prev_close = candles[i - 1].close
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return trs


def atr(candles: Sequence[Candle], period: int = 14) -> float:
    """Wilder's Average True Range of the latest bar (price units)."""
    if len(candles) < period + 1:
        raise ValueError(f"atr: need >= {period + 1} candles, got {len(candles)}")
    trs = true_ranges(candles)
    value = sum(trs[:period]) / period
    for i in range(period, len(trs)):
        value = (value * (period - 1) + trs[i]) / period
    return value


def bollinger(
    values: Sequence[float], period: int = 20, mult: float = 2.0
) -> tuple[float, float, float]:
    """Latest Bollinger Bands as ``(upper, mid, lower)`` (population std)."""
    if len(values) < period:
        raise ValueError(f"bollinger: need >= {period} values, got {len(values)}")
    window = [float(v) for v in values[-period:]]
    mid = sum(window) / period
    sd = statistics.pstdev(window)
    return (mid + mult * sd, mid, mid - mult * sd)


def vwap(candles: Sequence[Candle]) -> float:
    """Volume-weighted average price over the supplied candles (typical price)."""
    num = 0.0
    den = 0.0
    for c in candles:
        typical = (c.high + c.low + c.close) / 3.0
        num += typical * c.volume
        den += c.volume
    if den == 0.0:
        raise ValueError("vwap: total volume is zero")
    return num / den


def rolling_mean(values: Sequence[float], period: int) -> float:
    """Mean of the last ``period`` values."""
    if len(values) < period:
        raise ValueError(f"rolling_mean: need >= {period} values, got {len(values)}")
    return sum(float(v) for v in values[-period:]) / period
