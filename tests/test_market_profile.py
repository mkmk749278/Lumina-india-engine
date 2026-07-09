"""Time-of-day volume normalisation (src/market_profile.py)."""

from __future__ import annotations

from datetime import datetime, time

import config
from src.market.candle import Candle
from src.market_profile import tod_adjusted_volume_ratio, tod_factor


def _bar(hh: int, mm: int, volume: float) -> Candle:
    ts = config.IST.localize(datetime(2026, 7, 8, hh, mm))
    return Candle(ts=ts, open=100.0, high=100.5, low=99.5, close=100.0, volume=volume, oi=0.0)


def test_open_expects_more_volume_than_lunch() -> None:
    assert tod_factor(time(9, 20)) > tod_factor(time(10, 30)) > tod_factor(time(12, 30))


def test_close_expects_more_volume_than_midday() -> None:
    assert tod_factor(time(15, 15)) > tod_factor(time(14, 0))


def test_preopen_maps_to_first_bucket() -> None:
    assert tod_factor(time(9, 0)) == tod_factor(time(9, 15))


def test_midday_surge_outranks_equal_opening_volume() -> None:
    # Same absolute last-bar volume against the same average: at 12:30 (lunch
    # lull) it is a genuine surge, at 09:20 it is just the open.
    base = [_bar(10, 0 + i, 1000.0) for i in range(0, 20)]
    lunch = tod_adjusted_volume_ratio([*base, _bar(12, 30, 2000.0)], scan_time=time(12, 40))
    opening = tod_adjusted_volume_ratio([*base, _bar(9, 20, 2000.0)], scan_time=time(9, 30))
    assert lunch > opening


def test_building_bar_is_pro_rated_up() -> None:
    bars = [_bar(10, 0 + i, 1000.0) for i in range(0, 20)]
    current = _bar(10, 20, 600.0)
    # 60s into a 300s bucket -> fraction clamps at min 0.3 -> 600/0.3 = 2000 adj.
    early = tod_adjusted_volume_ratio([*bars, current], scan_time=time(10, 21))
    done = tod_adjusted_volume_ratio([*bars, current], scan_time=time(10, 25))
    assert early > done


def test_insufficient_data_returns_zero() -> None:
    assert tod_adjusted_volume_ratio([_bar(10, 0, 1000.0)], scan_time=time(10, 2)) == 0.0
