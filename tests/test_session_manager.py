"""SessionManager: state machine, hard session gate, close countdown."""

from __future__ import annotations

from datetime import datetime

import config
from src.session.session_manager import SessionManager, SessionState
from tests.conftest import MakeHolidays


# 2026-07-08 is a Wednesday (trading day); 2026-07-11 is a Saturday.
def _ist(dt: datetime) -> datetime:
    return config.IST.localize(dt)


def test_state_timeline_on_trading_day(make_holidays: MakeHolidays) -> None:
    sm = SessionManager(make_holidays())
    assert sm.current_state(_ist(datetime(2026, 7, 8, 8, 0))) is SessionState.CLOSED
    assert sm.current_state(_ist(datetime(2026, 7, 8, 9, 5))) is SessionState.PRE_OPEN
    assert sm.current_state(_ist(datetime(2026, 7, 8, 10, 0))) is SessionState.OPEN
    assert sm.current_state(_ist(datetime(2026, 7, 8, 15, 22))) is SessionState.CLOSING
    assert sm.current_state(_ist(datetime(2026, 7, 8, 15, 31))) is SessionState.CLOSED


def test_weekend_is_closed(make_holidays: MakeHolidays) -> None:
    sm = SessionManager(make_holidays())
    assert sm.current_state(_ist(datetime(2026, 7, 11, 10, 0))) is SessionState.CLOSED


def test_holiday_is_closed(make_holidays: MakeHolidays) -> None:
    sm = SessionManager(make_holidays(["2026-07-08"]))
    assert sm.current_state(_ist(datetime(2026, 7, 8, 10, 0))) is SessionState.CLOSED


def test_signals_allowed_only_when_open(make_holidays: MakeHolidays) -> None:
    sm = SessionManager(make_holidays())
    assert sm.signals_allowed(_ist(datetime(2026, 7, 8, 10, 0))) is True
    assert sm.signals_allowed(_ist(datetime(2026, 7, 8, 15, 22))) is False


def test_minutes_to_close(make_holidays: MakeHolidays) -> None:
    sm = SessionManager(make_holidays())
    assert sm.minutes_to_close(_ist(datetime(2026, 7, 8, 15, 0))) == 30
    assert sm.minutes_to_close(_ist(datetime(2026, 7, 8, 16, 0))) == 0


def test_dev_mode_forces_open(make_holidays: MakeHolidays, monkeypatch) -> None:
    monkeypatch.setattr(config, "INDIA_DEV_MODE", True)
    sm = SessionManager(make_holidays())
    # Even on a weekend at midnight, dev mode reports OPEN.
    assert sm.current_state(_ist(datetime(2026, 7, 11, 0, 0))) is SessionState.OPEN
