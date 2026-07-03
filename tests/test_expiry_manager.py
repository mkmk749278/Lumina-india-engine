"""ExpiryManager: weekly resolution, intraday roll, holiday shift, symbol format."""

from __future__ import annotations

from datetime import date, datetime

import pytest

import config
from src.session.expiry_manager import ExpiryManager
from tests.conftest import MakeHolidays

# 2026-07-08 is a Wednesday; the nearest Tuesday expiry is 2026-07-14.
WED = datetime(2026, 7, 8, 12, 0)
EXPIRY_TUE = date(2026, 7, 14)


def _ist(dt: datetime) -> datetime:
    return config.IST.localize(dt)


def test_nearest_weekly_expiry(make_holidays: MakeHolidays) -> None:
    em = ExpiryManager(make_holidays())
    assert em.get_expiry_date(_ist(WED)) == EXPIRY_TUE


def test_symbol_format(make_holidays: MakeHolidays) -> None:
    em = ExpiryManager(make_holidays())
    assert em.get_active_symbol("NIFTY", _ist(WED)) == "NSE:NIFTY26JULFUT"
    assert em.get_active_symbol("BANKNIFTY", _ist(WED)) == "NSE:BANKNIFTY26JULFUT"


def test_intraday_roll_on_expiry_day(make_holidays: MakeHolidays) -> None:
    em = ExpiryManager(make_holidays())
    # Before the roll hour on expiry Tuesday -> still this week.
    assert em.get_expiry_date(_ist(datetime(2026, 7, 14, 8, 0))) == EXPIRY_TUE
    assert em.is_expiry_day(_ist(datetime(2026, 7, 14, 8, 0))) is True
    # After the roll hour -> next week's Tuesday.
    assert em.get_expiry_date(_ist(datetime(2026, 7, 14, 10, 0))) == date(2026, 7, 21)


def test_holiday_shifts_expiry_earlier(make_holidays: MakeHolidays) -> None:
    # Expiry Tuesday is a holiday -> shift to Monday 13th (a trading day).
    em = ExpiryManager(make_holidays(["2026-07-14"]))
    assert em.get_expiry_date(_ist(WED)) == date(2026, 7, 13)


def test_disallowed_base_rejected(make_holidays: MakeHolidays) -> None:
    em = ExpiryManager(make_holidays())
    with pytest.raises(ValueError, match="not permitted"):
        em.get_active_symbol("CRUDEOIL", _ist(WED))
