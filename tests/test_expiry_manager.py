"""ExpiryManager: monthly futures contract expiry (last Tuesday), weekly
gamma-expiry flag, intraday roll, holiday shift, and symbol format."""

from __future__ import annotations

from datetime import date, datetime

import pytest

import config
from src.session.expiry_manager import ExpiryManager
from tests.conftest import MakeHolidays

# 2026-07-08 is a Wednesday. July 2026 Tuesdays: 7, 14, 21, 28.
# Monthly futures expiry = last Tuesday = 2026-07-28.
# Nearest weekly (Tuesday) expiry from the 8th = 2026-07-14.
WED = datetime(2026, 7, 8, 12, 0)
CONTRACT_EXPIRY = date(2026, 7, 28)
WEEKLY_EXPIRY = date(2026, 7, 14)


def _ist(dt: datetime) -> datetime:
    return config.IST.localize(dt)


# ── Monthly futures contract expiry ──────────────────────────────────


def test_contract_expiry_is_last_tuesday(make_holidays: MakeHolidays) -> None:
    em = ExpiryManager(make_holidays())
    assert em.get_contract_expiry_date(_ist(WED)) == CONTRACT_EXPIRY


def test_symbol_uses_contract_month(make_holidays: MakeHolidays) -> None:
    em = ExpiryManager(make_holidays())
    assert em.get_active_symbol("NIFTY", _ist(WED)) == "NSE:NIFTY26JULFUT"
    assert em.get_active_symbol("BANKNIFTY", _ist(WED)) == "NSE:BANKNIFTY26JULFUT"


def test_days_to_expiry_counts_to_monthly(make_holidays: MakeHolidays) -> None:
    em = ExpiryManager(make_holidays())
    # 2026-07-28 minus 2026-07-08 = 20 days.
    assert em.days_to_expiry(_ist(WED)) == 20


def test_contract_rolls_after_expiry_day(make_holidays: MakeHolidays) -> None:
    em = ExpiryManager(make_holidays())
    # On the last Tuesday, before the roll hour -> still this month.
    assert em.get_contract_expiry_date(_ist(datetime(2026, 7, 28, 8, 0))) == CONTRACT_EXPIRY
    # After the roll hour -> next month's last Tuesday (2026-08-25).
    assert em.get_contract_expiry_date(_ist(datetime(2026, 7, 28, 10, 0))) == date(2026, 8, 25)
    # A day past expiry -> already on the next contract.
    assert em.get_contract_expiry_date(_ist(datetime(2026, 7, 29, 12, 0))) == date(2026, 8, 25)


def test_symbol_rolls_to_next_month_after_expiry(make_holidays: MakeHolidays) -> None:
    em = ExpiryManager(make_holidays())
    assert em.get_active_symbol("NIFTY", _ist(datetime(2026, 7, 29, 12, 0))) == "NSE:NIFTY26AUGFUT"


def test_contract_expiry_shifts_off_holiday(make_holidays: MakeHolidays) -> None:
    # Last Tuesday (2026-07-28) is a holiday -> shift to Monday 27th.
    em = ExpiryManager(make_holidays(["2026-07-28"]))
    assert em.get_contract_expiry_date(_ist(WED)) == date(2026, 7, 27)


# ── Weekly (Tuesday) expiry — gamma / IB16 ───────────────────────────


def test_weekly_expiry_is_nearest_tuesday(make_holidays: MakeHolidays) -> None:
    em = ExpiryManager(make_holidays())
    assert em.get_weekly_expiry_date(_ist(WED)) == WEEKLY_EXPIRY


def test_is_weekly_expiry_day(make_holidays: MakeHolidays) -> None:
    em = ExpiryManager(make_holidays())
    assert em.is_weekly_expiry_day(_ist(datetime(2026, 7, 14, 8, 0))) is True
    assert em.is_weekly_expiry_day(_ist(WED)) is False


def test_weekly_expiry_rolls_after_roll_hour(make_holidays: MakeHolidays) -> None:
    em = ExpiryManager(make_holidays())
    assert em.get_weekly_expiry_date(_ist(datetime(2026, 7, 14, 8, 0))) == WEEKLY_EXPIRY
    assert em.get_weekly_expiry_date(_ist(datetime(2026, 7, 14, 10, 0))) == date(2026, 7, 21)


def test_weekly_expiry_shifts_off_holiday(make_holidays: MakeHolidays) -> None:
    # Weekly Tuesday (2026-07-14) is a holiday -> shift to Monday 13th.
    em = ExpiryManager(make_holidays(["2026-07-14"]))
    assert em.get_weekly_expiry_date(_ist(WED)) == date(2026, 7, 13)


def test_disallowed_base_rejected(make_holidays: MakeHolidays) -> None:
    em = ExpiryManager(make_holidays())
    with pytest.raises(ValueError, match="not permitted"):
        em.get_active_symbol("CRUDEOIL", _ist(WED))
