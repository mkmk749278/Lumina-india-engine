"""Market-regime classification.

Evaluators consult the higher-timeframe regime to decide alignment (spec
``regime_60m`` / ``regime_daily``: TRENDING_UP | TRENDING_DOWN | RANGING |
QUIET). This module is the single source of that label.

Heuristic (tunable, deliberately conservative):
  1. Too little history → RANGING (never claim a trend we can't see).
  2. ATR% below ``REGIME_QUIET_ATR_PCT`` → QUIET (low volatility).
  3. Fast EMA above slow EMA and price above fast EMA → TRENDING_UP; mirror →
     TRENDING_DOWN; otherwise RANGING.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum

import config
from src.indicators import atr, ema
from src.market.candle import Candle


class Regime(StrEnum):
    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    RANGING = "RANGING"
    QUIET = "QUIET"


def classify(candles: Sequence[Candle], fast: int = 21, slow: int = 55) -> Regime:
    """Classify the regime of ``candles`` using an EMA stack + ATR% filter."""
    closes = [c.close for c in candles]
    if len(closes) < slow + 1:
        return Regime.RANGING
    try:
        atr_value = atr(candles)
    except ValueError:
        return Regime.RANGING
    last = closes[-1]
    atr_pct = (atr_value / last * 100.0) if last else 0.0
    if atr_pct < config.REGIME_QUIET_ATR_PCT:
        return Regime.QUIET
    fast_ema = ema(closes, fast)
    slow_ema = ema(closes, slow)
    if fast_ema > slow_ema and last > fast_ema:
        return Regime.TRENDING_UP
    if fast_ema < slow_ema and last < fast_ema:
        return Regime.TRENDING_DOWN
    return Regime.RANGING
