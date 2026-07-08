"""prev_session_levels — previous-session OHLC derivation; candle aggregation."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import config
from src.broker.history_utils import aggregate_candles, prev_session_levels
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


# ── aggregate_candles ────────────────────────────────────────────────

def _seq(n: int) -> list[Candle]:
    base = config.IST.localize(datetime(2026, 7, 6, 9, 15))
    return [
        Candle(
            ts=base + timedelta(minutes=5 * i),
            open=100.0 + i,
            high=102.0 + i,
            low=99.0 + i,
            close=101.0 + i,
            volume=1000.0,
        )
        for i in range(n)
    ]


def test_aggregate_60m_buckets_by_clock_time() -> None:
    # 24 bars from 09:15: clock-hour buckets are 09:00 (09:15–09:55, 9 bars),
    # 10:00 (12 bars), 11:00 (3 bars) — same boundaries the live tick store
    # uses, so seeded and live 60m bars line up.
    c5 = _seq(24)
    c60 = aggregate_candles(c5, 60)
    assert len(c60) == 3
    assert [(c.ts.hour, c.ts.minute) for c in c60] == [(9, 0), (10, 0), (11, 0)]
    assert c60[0].open == c5[0].open
    assert c60[0].close == c5[8].close
    assert c60[0].high == max(c.high for c in c5[:9])
    assert c60[0].volume == sum(c.volume for c in c5[:9])
    assert c60[1].open == c5[9].open
    assert c60[1].close == c5[20].close


def test_aggregate_keeps_partial_trailing_bucket() -> None:
    # 7 bars from 09:15 -> 15m buckets 09:15 (3), 09:30 (3), 09:45 (1).
    # The partial tail is kept; callers seeding a live store trim the
    # currently-forming bucket explicitly against "now".
    assert len(aggregate_candles(_seq(7), 15)) == 3


def test_aggregate_never_merges_across_days() -> None:
    # A group must never span the overnight gap even when bar counts would
    # let the old fixed-count grouping merge two sessions into one candle.
    day1 = _seq(75)  # full session 09:15–15:25
    day2 = [
        Candle(
            ts=c.ts + timedelta(days=1),
            open=c.open,
            high=c.high,
            low=c.low,
            close=c.close,
            volume=c.volume,
        )
        for c in _seq(12)
    ]
    c60 = aggregate_candles(day1 + day2, 60)
    day1_date = day1[0].ts.date()
    day2_date = day2[0].ts.date()
    assert {c.ts.date() for c in c60} == {day1_date, day2_date}


def test_aggregate_60m_yields_enough_bars_for_regime() -> None:
    # ~11 trading days of 5m (~825 bars) -> >=56 60m bars so the EMA55 regime
    # can classify at session open. Guards the seed-window rationale.
    c60 = aggregate_candles(_seq(825), 60)
    assert len(c60) >= 56
