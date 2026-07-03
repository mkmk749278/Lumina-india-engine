"""Shared helpers for turning raw historical candles into context inputs."""

from __future__ import annotations

from datetime import date

from src.market.candle import Candle


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
