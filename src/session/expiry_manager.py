"""Expiry resolution and active-contract symbol construction.

The traded instrument in Phase 1 is the index **future**, which on NSE is a
*monthly* contract expiring on the **last Tuesday** of its contract month
(SEBI-driven revision effective 1-Sep-2025; before that, last Thursday). There
is no weekly future — weekly cadence is an options-only construct.

Two distinct expiry concepts therefore live here:

* **Contract (monthly) expiry** — ``get_contract_expiry_date`` /
  ``days_to_expiry`` / ``get_active_symbol``. Drives the traded symbol, the
  contract roll, and the expiry shown on the signal card.
* **Weekly expiry (every Tuesday)** — ``is_weekly_expiry_day``. Drives the
  gamma-squeeze evaluator and the IB16 expiry-day behaviour, which key off the
  weekly options expiry, not the futures roll.

If a nominal expiry weekday is an NSE holiday, expiry shifts to the preceding
trading day (standard NSE rule).
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

    def _shift_off_holiday(self, expiry: date) -> date:
        """Shift *expiry* to the preceding trading day if it is a holiday."""
        while not self._holidays.is_trading_day(expiry):
            expiry -= timedelta(days=1)
        return expiry

    # ── Monthly futures contract expiry (the traded instrument) ──────────

    def _last_weekday_of_month(self, year: int, month: int) -> date:
        """Last ``EXPIRY_WEEKDAY`` (Tuesday) of *month*, holiday-adjusted."""
        if month == 12:
            first_next = date(year + 1, 1, 1)
        else:
            first_next = date(year, month + 1, 1)
        last_day = first_next - timedelta(days=1)
        offset = (last_day.weekday() - config.EXPIRY_WEEKDAY) % 7
        return self._shift_off_holiday(last_day - timedelta(days=offset))

    def get_contract_expiry_date(self, now: datetime | None = None) -> date:
        """Active monthly futures expiry as of ``now`` (IST).

        Once the current month's expiry has passed — or on expiry day itself
        past :data:`config.EXPIRY_ROLL_HOUR` — the contract rolls to next
        month's last Tuesday.
        """
        moment = self._now(now)
        today = moment.date()
        this_month = self._last_weekday_of_month(today.year, today.month)
        rolled = today > this_month or (
            today == this_month and moment.hour >= config.EXPIRY_ROLL_HOUR
        )
        if not rolled:
            return this_month
        nxt_year = today.year + (1 if today.month == 12 else 0)
        nxt_month = 1 if today.month == 12 else today.month + 1
        return self._last_weekday_of_month(nxt_year, nxt_month)

    def days_to_expiry(self, now: datetime | None = None) -> int:
        """Whole days to the active *futures* (monthly) contract expiry."""
        moment = self._now(now)
        return (self.get_contract_expiry_date(moment) - moment.date()).days

    def get_active_symbol(self, base: str, now: datetime | None = None) -> str:
        """Fyers v3 futures symbol, e.g. ``NSE:NIFTY26JULFUT``.

        The month/year come from the monthly contract expiry, so the symbol
        rolls to next month only after the last-Tuesday expiry — never on an
        intra-month weekly Tuesday.

        Format verified against Fyers' public NSE_FO symbol master — no
        suffix after ``FUT`` (the spec's ``-FF`` was wrong).
        """
        self._require_allowed(base)
        expiry = self.get_contract_expiry_date(now)
        year = expiry.strftime("%y")
        month = expiry.strftime("%b").upper()
        return f"NSE:{base}{year}{month}FUT"

    def is_contract_expiry_day(self, now: datetime | None = None) -> bool:
        """True if today is this month's (holiday-adjusted) contract expiry.

        Stock F&O has no weekly cadence — IB16 expiry-day behaviour for stock
        bases keys off the monthly contract expiry instead of the weekly flag.
        """
        moment = self._now(now)
        today = moment.date()
        return self._last_weekday_of_month(today.year, today.month) == today

    # ── Weekly expiry (Tuesday) — gamma squeeze / IB16 behaviour ─────────

    def get_weekly_expiry_date(self, now: datetime | None = None) -> date:
        """Nearest weekly (Tuesday) expiry as of ``now`` (IST), holiday-shifted.

        On the weekly expiry weekday past :data:`config.EXPIRY_ROLL_HOUR` the
        current week is considered done and we roll to next week.
        """
        moment = self._now(now)
        today = moment.date()
        days_ahead = (config.EXPIRY_WEEKDAY - today.weekday()) % 7
        if days_ahead == 0 and moment.hour >= config.EXPIRY_ROLL_HOUR:
            days_ahead = 7
        return self._shift_off_holiday(today + timedelta(days=days_ahead))

    def is_weekly_expiry_day(self, now: datetime | None = None) -> bool:
        """True if today is this week's (holiday-adjusted) weekly expiry.

        Drives EXPIRY_GAMMA_SQUEEZE + IB16 expiry-day behaviour, which follow
        the weekly options expiry rather than the monthly futures roll.
        """
        moment = self._now(now)
        start_of_day = config.IST.localize(
            datetime.combine(moment.date(), datetime.min.time())
        )
        return self.get_weekly_expiry_date(start_of_day) == moment.date()

    @staticmethod
    def _require_allowed(base: str) -> None:
        if base.upper() not in config.ALLOWED_BASES:
            raise ValueError(
                f"base {base!r} is not permitted; ALLOWED_BASES="
                f"{config.ALLOWED_BASES} (index futures only, OWNER_BRIEF IB1)"
            )
