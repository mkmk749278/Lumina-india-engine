"""Weekly expiry resolution and active-contract symbol construction.

Resolves the nearest active weekly expiry and the Fyers v3 trading symbol for a
base (OWNER_BRIEF: near-weekly contract only). If the nominal expiry weekday is
an NSE holiday, expiry shifts to the preceding trading day (standard NSE rule).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import config
from src.session.holiday_manager import HolidayManager


class ExpiryManager:
    """Owns expiry-date resolution and Fyers symbol formatting."""

    def __init__(self, holidays: HolidayManager | None = None) -> None:
        self._holidays = holidays or HolidayManager()

    def _now(self, now: datetime | None) -> datetime:
        return now or datetime.now(config.IST)

    def get_expiry_date(self, now: datetime | None = None) -> date:
        """Nearest active weekly expiry as of ``now`` (IST).

        On the expiry weekday, once past :data:`config.EXPIRY_ROLL_HOUR` the
        current contract is considered expired and we roll to next week. If the
        resulting date is a holiday, it shifts back to the prior trading day.
        """
        moment = self._now(now)
        today = moment.date()
        days_ahead = (config.EXPIRY_WEEKDAY - today.weekday()) % 7
        if days_ahead == 0 and moment.hour >= config.EXPIRY_ROLL_HOUR:
            days_ahead = 7
        expiry = today + timedelta(days=days_ahead)
        while not self._holidays.is_trading_day(expiry):
            expiry -= timedelta(days=1)
        return expiry

    def is_expiry_day(self, now: datetime | None = None) -> bool:
        """True if today is this week's expiry (ignoring the intraday roll)."""
        moment = self._now(now)
        start_of_day = config.IST.localize(
            datetime.combine(moment.date(), datetime.min.time())
        )
        return self.get_expiry_date(start_of_day) == moment.date()

    def days_to_expiry(self, now: datetime | None = None) -> int:
        moment = self._now(now)
        return (self.get_expiry_date(moment) - moment.date()).days

    def get_active_symbol(self, base: str, now: datetime | None = None) -> str:
        """Fyers v3 futures symbol, e.g. ``NSE:NIFTY26JULFUT``.

        Format verified against Fyers' public NSE_FO symbol master — no
        suffix after ``FUT`` (the spec's ``-FF`` was wrong).
        """
        self._require_allowed(base)
        expiry = self.get_expiry_date(now)
        year = expiry.strftime("%y")
        month = expiry.strftime("%b").upper()
        return f"NSE:{base}{year}{month}FUT"

    @staticmethod
    def _require_allowed(base: str) -> None:
        if base.upper() not in config.ALLOWED_BASES:
            raise ValueError(
                f"base {base!r} is not permitted; ALLOWED_BASES="
                f"{config.ALLOWED_BASES} (index futures only, OWNER_BRIEF IB1)"
            )
