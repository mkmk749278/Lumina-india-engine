"""Candlestick rejection patterns (pure geometry over the Candle model).

The reversal evaluators (VIX/PCR extremes, failed-auction reclaim, OI-spike)
key off a rejection candle at a level. These primitives detect the two forms
we use: engulfing (needs the prior bar) and pin bars (single bar). Thresholds
are function params so an evaluator can tune sensitivity without new config.
"""

from __future__ import annotations

from src.market.candle import Candle


def _body(c: Candle) -> float:
    return abs(c.close - c.open)


def _upper_wick(c: Candle) -> float:
    return c.high - max(c.open, c.close)


def _lower_wick(c: Candle) -> float:
    return min(c.open, c.close) - c.low


def is_bullish_engulfing(prev: Candle, cur: Candle) -> bool:
    return (
        cur.close > cur.open
        and prev.close < prev.open
        and cur.close >= prev.open
        and cur.open <= prev.close
    )


def is_bearish_engulfing(prev: Candle, cur: Candle) -> bool:
    return (
        cur.close < cur.open
        and prev.close > prev.open
        and cur.open >= prev.close
        and cur.close <= prev.open
    )


def is_bullish_pin_bar(cur: Candle, wick_ratio: float = 2.0) -> bool:
    body = _body(cur)
    return (
        body > 0
        and _lower_wick(cur) >= wick_ratio * body
        and _upper_wick(cur) <= body
        and cur.close >= cur.open
    )


def is_bearish_pin_bar(cur: Candle, wick_ratio: float = 2.0) -> bool:
    body = _body(cur)
    return (
        body > 0
        and _upper_wick(cur) >= wick_ratio * body
        and _lower_wick(cur) <= body
        and cur.close <= cur.open
    )


def is_bullish_rejection(cur: Candle, prev: Candle | None = None) -> bool:
    if is_bullish_pin_bar(cur):
        return True
    return prev is not None and is_bullish_engulfing(prev, cur)


def is_bearish_rejection(cur: Candle, prev: Candle | None = None) -> bool:
    if is_bearish_pin_bar(cur):
        return True
    return prev is not None and is_bearish_engulfing(prev, cur)
