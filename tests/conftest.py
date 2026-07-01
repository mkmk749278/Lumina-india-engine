"""Shared test fixtures."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable

import pytest

from src.session.holiday_manager import HolidayManager

MakeHolidays = Callable[..., HolidayManager]


@pytest.fixture
def make_holidays(tmp_path) -> MakeHolidays:
    """Factory: build a HolidayManager from an ad-hoc calendar file."""

    def _make(holidays: Iterable[str] = (), verified: bool = True) -> HolidayManager:
        path = tmp_path / "nse_holidays.json"
        path.write_text(
            json.dumps({"verified": verified, "holidays": list(holidays)}),
            encoding="utf-8",
        )
        return HolidayManager(str(path))

    return _make
