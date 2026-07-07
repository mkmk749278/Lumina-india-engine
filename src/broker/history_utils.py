"""Shared helpers for turning raw historical candles into context inputs."""

from __future__ import annotations

from datetime import date

from src.market.candle import Candle


def aggregate_candles(candles_5m: list[Candle], tf_minutes: int) -> list[Candle]:
    """Aggregate a 5m candle series into a higher timeframe (15m / 60m).

    Groups by fixed bucket count (``tf_minutes // 5``). Used to seed the tick
    store's higher-timeframe ring buffers directly from a long historical fetch
    so the 60m regime (EMA21/EMA55 -> needs ~56 bars) can form at session open,
    instead of waiting ~9 trading days to accrue live. Bars are grouped from the
    oldest so the most recent (possibly partial) tail is what gets dropped.
    """
    bars_per_htf = tf_minutes // 5
    if bars_per_htf <= 0 or not candles_5m:
        return list(candles_5m)
    result: list[Candle] = []
    i = 0
    n = len(candles_5m)
    while i + bars_per_htf <= n:
        group = candles_5m[i : i + bars_per_htf]
        result.append(
            Candle(
                ts=group[0].ts,
                open=group[0].open,
                high=max(c.high for c in group),
                low=min(c.low for c in group),
                close=group[-1].close,
                volume=sum(c.volume for c in group),
            )
        )
        i += bars_per_htf
    return result


def prev_session_levels(
    candles: list[Candle], today: date
) -> tuple[float, float, float] | None:
    """(high, low, close) of the most recent session strictly before *today*.

    The seed window spans the previous trading session plus today; candles
    are bucketed by calendar date and the latest pre-today date wins (so a
    Monday correctly picks Friday, skipping the weekend gap).
    """
    prior = [c for c in candles if c.ts.date() < today]
    if not prior:
        return None
    last_session = max(c.ts.date() for c in prior)
    session = [c for c in prior if c.ts.date() == last_session]
    return (
        max(c.high for c in session),
        min(c.low for c in session),
        session[-1].close,
    )
