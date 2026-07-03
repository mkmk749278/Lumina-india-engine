"""prev_session_levels — previous-session OHLC derivation."""

from __future__ import annotations

from datetime import date, datetime

import config
from src.broker.history_utils import prev_session_levels
from src.market.candle import Candle


def _candle(day: str, hhmm: str, high: float, low: float, close: float) -> Candle:
    ts = config.IST.localize(datetime.fromisoformat(f"{day}T{hhmm}:00"))
    return Candle(ts=ts, open=close, high=high, low=low, close=close, volume=100)


def test_picks_latest_pre_today_session_across_weekend() -> None:
    candles = [
        _candle("2026-07-02", "10:00", 24500, 24400, 24450),  # Thursday
        _candle("2026-07-03", "10:00", 24600, 24350, 24500),  # Friday
        _candle("2026-07-03", "14:00", 24700, 24450, 24650),  # Friday
        _candle("2026-07-06", "09:20", 24800, 24700, 24750),  # Monday (today)
    ]
    levels = prev_session_levels(candles, date(2026, 7, 6))
    assert levels is not None
    high, low, close = levels
    assert high == 24700  # Friday's high, not Thursday's
    assert low == 24350
    assert close == 24650  # Friday's last close


def test_no_prior_session_returns_none() -> None:
    candles = [_candle("2026-07-06", "09:20", 24800, 24700, 24750)]
    assert prev_session_levels(candles, date(2026, 7, 6)) is None


def test_empty_returns_none() -> None:
    assert prev_session_levels([], date(2026, 7, 6)) is None
