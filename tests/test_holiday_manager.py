"""HolidayManager: weekend + holiday gating and trading-day navigation."""

from __future__ import annotations

from datetime import date

from tests.conftest import MakeHolidays

# 2026-01-26 (Mon) Republic Day; surrounding week used for navigation checks.
REPUBLIC_DAY = date(2026, 1, 26)


def test_is_holiday_and_verified(make_holidays: MakeHolidays) -> None:
    hm = make_holidays(["2026-01-26"], verified=True)
    assert hm.is_verified() is True
    assert hm.is_holiday("2026-01-26") is True
    assert hm.is_holiday(REPUBLIC_DAY) is True
    assert hm.is_holiday("2026-01-27") is False


def test_unverified_flag(make_holidays: MakeHolidays) -> None:
    hm = make_holidays([], verified=False)
    assert hm.is_verified() is False


def test_weekend_detection(make_holidays: MakeHolidays) -> None:
    hm = make_holidays()
    assert hm.is_weekend(date(2026, 1, 24)) is True   # Saturday
    assert hm.is_weekend(date(2026, 1, 25)) is True   # Sunday
    assert hm.is_weekend(date(2026, 1, 27)) is False  # Tuesday


def test_trading_day(make_holidays: MakeHolidays) -> None:
    hm = make_holidays(["2026-01-26"])
    assert hm.is_trading_day(REPUBLIC_DAY) is False      # holiday
    assert hm.is_trading_day(date(2026, 1, 24)) is False  # Saturday
    assert hm.is_trading_day(date(2026, 1, 27)) is True   # Tuesday


def test_next_and_previous_trading_day(make_holidays: MakeHolidays) -> None:
    hm = make_holidays(["2026-01-26"])
    # Fri 23rd -> skip Sat/Sun/Mon(holiday) -> Tue 27th
    assert hm.next_trading_day(date(2026, 1, 23)) == date(2026, 1, 27)
    # Back from Tue 27th -> skip Mon(holiday)/Sun/Sat -> Fri 23rd
    assert hm.previous_trading_day(date(2026, 1, 27)) == date(2026, 1, 23)
