"""IndiaTickStore — tick aggregation, ring buffers, opening range, day stats."""

from __future__ import annotations

from datetime import datetime, timedelta

import config
from src.data.india_tick_store import IndiaTickStore, _bar_open_time
from src.market.candle import Candle

IST = config.IST
_SYM = "NSE:NIFTY26JULFUT-FF"


_BASE_DT = IST.localize(datetime(2026, 7, 7, 0, 0, 0))


def _ist(h: int, m: int, s: int = 0) -> datetime:
    return _BASE_DT + timedelta(hours=h, minutes=m, seconds=s)


# --- bar_open_time ---

def test_bar_open_truncates_5m() -> None:
    ts = _ist(10, 13, 45)
    assert _bar_open_time(ts, 5).minute == 10

def test_bar_open_truncates_15m() -> None:
    ts = _ist(10, 23, 0)
    assert _bar_open_time(ts, 15).minute == 15

def test_bar_open_truncates_60m() -> None:
    ts = _ist(10, 59, 0)
    assert _bar_open_time(ts, 60).minute == 0


# --- seed ---

def test_seed_loads_5m_candles() -> None:
    store = IndiaTickStore()
    candles = [
        Candle(ts=_ist(9, 15 + i * 5), open=100.0, high=101.0, low=99.0,
               close=100.5, volume=1000.0)
        for i in range(20)
    ]
    store.seed(_SYM, candles)
    assert len(store.get_candles_5m(_SYM)) == 20

def test_seed_aggregates_15m_from_5m() -> None:
    store = IndiaTickStore()
    candles = [
        Candle(ts=_ist(9, 15 + i * 5), open=100.0 + i, high=102.0 + i,
               low=99.0 + i, close=101.0 + i, volume=1000.0)
        for i in range(15)
    ]
    store.seed(_SYM, candles)
    c15 = store.get_candles_15m(_SYM)
    assert len(c15) == 5
    assert c15[0].open == 100.0
    assert c15[0].close == 103.0

def test_seed_aggregates_60m_from_5m() -> None:
    store = IndiaTickStore()
    candles = [
        Candle(ts=_ist(9, 15 + i * 5), open=100.0, high=102.0,
               low=99.0, close=101.0, volume=500.0)
        for i in range(12)
    ]
    store.seed(_SYM, candles)
    c60 = store.get_candles_60m(_SYM)
    assert len(c60) == 1
    assert c60[0].volume == 6000.0

def test_seed_with_explicit_15m() -> None:
    store = IndiaTickStore()
    c5 = [
        Candle(ts=_ist(9, 15 + i * 5), open=100.0, high=101.0,
               low=99.0, close=100.0, volume=100.0)
        for i in range(6)
    ]
    c15 = [
        Candle(ts=_ist(9, 15 + i * 15), open=200.0, high=201.0,
               low=199.0, close=200.0, volume=300.0)
        for i in range(2)
    ]
    store.seed(_SYM, c5, candles_15m=c15)
    assert len(store.get_candles_15m(_SYM)) == 2
    assert store.get_candles_15m(_SYM)[0].open == 200.0


# --- on_tick candle building ---

def test_tick_builds_single_5m_candle() -> None:
    store = IndiaTickStore()
    base_ts = _ist(10, 0, 0)
    store.on_tick(_SYM, 24000.0, 100.0, base_ts)
    store.on_tick(_SYM, 24010.0, 50.0, base_ts + timedelta(seconds=30))
    store.on_tick(_SYM, 23990.0, 80.0, base_ts + timedelta(seconds=60))
    store.on_tick(_SYM, 24005.0, 70.0, base_ts + timedelta(seconds=120))

    candles = store.get_candles_5m(_SYM)
    assert len(candles) == 1
    bar = candles[0]
    assert bar.open == 24000.0
    assert bar.high == 24010.0
    assert bar.low == 23990.0
    assert bar.close == 24005.0
    assert bar.volume == 300.0

def test_tick_finalizes_on_new_bucket() -> None:
    store = IndiaTickStore()
    store.on_tick(_SYM, 24000.0, 100.0, _ist(10, 0, 0))
    store.on_tick(_SYM, 24010.0, 50.0, _ist(10, 2, 0))
    store.on_tick(_SYM, 24020.0, 200.0, _ist(10, 5, 0))

    candles = store.get_candles_5m(_SYM, include_building=False)
    assert len(candles) == 1
    assert candles[0].close == 24010.0

    all_candles = store.get_candles_5m(_SYM, include_building=True)
    assert len(all_candles) == 2
    assert all_candles[-1].close == 24020.0

def test_tick_builds_multiple_timeframes() -> None:
    store = IndiaTickStore()
    for minute in range(0, 20):
        store.on_tick(_SYM, 24000.0 + minute, 100.0, _ist(10, minute, 0))

    c5 = store.get_candles_5m(_SYM, include_building=False)
    c15 = store.get_candles_15m(_SYM, include_building=False)

    assert len(c5) == 3
    assert len(c15) == 1


# --- opening range ---

def test_opening_range_tracked() -> None:
    store = IndiaTickStore()
    store.on_tick(_SYM, 24000.0, 100.0, _ist(9, 15, 0))
    store.on_tick(_SYM, 24050.0, 100.0, _ist(9, 20, 0))
    store.on_tick(_SYM, 23980.0, 100.0, _ist(9, 30, 0))

    high, low = store.get_opening_range(_SYM)
    assert high == 24050.0
    assert low == 23980.0

def test_opening_range_locks_at_945() -> None:
    store = IndiaTickStore()
    store.on_tick(_SYM, 24000.0, 100.0, _ist(9, 15, 0))
    store.on_tick(_SYM, 24050.0, 100.0, _ist(9, 30, 0))
    store.on_tick(_SYM, 24100.0, 100.0, _ist(9, 45, 0))
    store.on_tick(_SYM, 24200.0, 100.0, _ist(9, 50, 0))

    high, low = store.get_opening_range(_SYM)
    assert high == 24050.0
    assert low == 24000.0

def test_opening_range_none_before_ticks() -> None:
    store = IndiaTickStore()
    high, low = store.get_opening_range(_SYM)
    assert high is None
    assert low is None


# --- day stats ---

def test_day_open_is_first_tick() -> None:
    store = IndiaTickStore()
    store.on_tick(_SYM, 24000.0, 100.0, _ist(9, 15, 0))
    store.on_tick(_SYM, 24050.0, 100.0, _ist(9, 20, 0))
    assert store.get_day_open(_SYM) == 24000.0

def test_intraday_extremes() -> None:
    store = IndiaTickStore()
    store.on_tick(_SYM, 24000.0, 100.0, _ist(9, 15, 0))
    store.on_tick(_SYM, 24100.0, 100.0, _ist(10, 0, 0))
    store.on_tick(_SYM, 23900.0, 100.0, _ist(11, 0, 0))
    store.on_tick(_SYM, 24050.0, 100.0, _ist(12, 0, 0))

    assert store.get_intraday_high(_SYM) == 24100.0
    assert store.get_intraday_low(_SYM) == 23900.0


# --- reset ---

def test_reset_clears_intraday_state() -> None:
    store = IndiaTickStore()
    store.on_tick(_SYM, 24000.0, 100.0, _ist(9, 15, 0))
    store.reset_day()

    assert store.get_day_open(_SYM) == 0.0
    assert store.get_opening_range(_SYM) == (None, None)


# --- volume average ---

def test_volume_avg_5m() -> None:
    store = IndiaTickStore()
    candles = [
        Candle(ts=_ist(9, 15 + i * 5), open=100.0, high=101.0, low=99.0,
               close=100.0, volume=float(1000 + i * 100))
        for i in range(20)
    ]
    store.seed(_SYM, candles)
    avg = store.get_volume_avg(_SYM, "5m", 20)
    expected = sum(1000 + i * 100 for i in range(20)) / 20
    assert abs(avg - expected) < 0.01


# --- ATR ---

def test_atr14_5m_with_enough_bars() -> None:
    store = IndiaTickStore()
    candles = [
        Candle(ts=_ist(9, 15 + i * 5), open=24000.0 + i * 10,
               high=24010.0 + i * 10, low=23990.0 + i * 10,
               close=24005.0 + i * 10, volume=1000.0)
        for i in range(20)
    ]
    store.seed(_SYM, candles)
    atr_val = store.get_atr14_5m(_SYM)
    assert atr_val > 0.0

def test_atr14_5m_insufficient_bars_returns_zero() -> None:
    store = IndiaTickStore()
    candles = [
        Candle(ts=_ist(9, 15 + i * 5), open=100.0, high=101.0,
               low=99.0, close=100.0, volume=100.0)
        for i in range(5)
    ]
    store.seed(_SYM, candles)
    assert store.get_atr14_5m(_SYM) == 0.0


# --- has_data ---

def test_has_data_false_before_seed() -> None:
    store = IndiaTickStore()
    assert not store.has_data(_SYM)

def test_has_data_true_after_seed() -> None:
    store = IndiaTickStore()
    store.seed(_SYM, [
        Candle(ts=_ist(9, 15), open=100.0, high=101.0, low=99.0,
               close=100.0, volume=100.0)
    ])
    assert store.has_data(_SYM)
