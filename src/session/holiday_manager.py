"""NSE holiday calendar.

The engine must never scan, signal, or (Phase 2) trade on an NSE holiday or a
weekend (OWNER_BRIEF IB6, CLAUDE.md hard limits). This is the single source of
truth for "is the market meant to be open on date D".

The calendar is loaded from ``config/nse_holidays.json``. If that file is marked
``verified: false`` the manager logs a loud warning at construction — an
incomplete festival calendar silently trading on a real holiday is exactly the
kind of hidden problem the doctrine forbids.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import config
from src.utils import get_logger

log = get_logger(__name__)

_SATURDAY = 5


def _coerce(d: date | str) -> date:
    return d if isinstance(d, date) else date.fromisoformat(d)


class HolidayManager:
    """Loads and answers questions about the NSE trading calendar."""

    def __init__(self, path: str | None = None) -> None:
        self._path = Path(path or config.NSE_HOLIDAYS_FILE)
        self._holidays, self._verified = self._load()
        if not self._verified:
            log.warning(
                "NSE holiday calendar at {} is UNVERIFIED — populate the full "
                "official list and set verified=true before go-live.",
                self._path,
            )

    def _load(self) -> tuple[set[date], bool]:
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        holidays = {date.fromisoformat(s) for s in raw.get("holidays", [])}
        return holidays, bool(raw.get("verified", False))

    def is_verified(self) -> bool:
        """True once the calendar has been confirmed complete for the year."""
        return self._verified

    def is_holiday(self, d: date | str) -> bool:
        """True if ``d`` is a listed NSE holiday (weekends excluded here)."""
        return _coerce(d) in self._holidays

    def is_weekend(self, d: date | str) -> bool:
        return _coerce(d).weekday() >= _SATURDAY

    def is_trading_day(self, d: date | str) -> bool:
        """True only on a weekday that is not a listed holiday."""
        day = _coerce(d)
        return not self.is_weekend(day) and not self.is_holiday(day)

    def next_trading_day(self, d: date | str) -> date:
        """The first trading day strictly after ``d``."""
        day = _coerce(d) + timedelta(days=1)
        while not self.is_trading_day(day):
            day += timedelta(days=1)
        return day

    def previous_trading_day(self, d: date | str) -> date:
        """The first trading day strictly before ``d``."""
        day = _coerce(d) - timedelta(days=1)
        while not self.is_trading_day(day):
            day -= timedelta(days=1)
        return day
