"""Market-session state machine and the hard session gate.

This is the authoritative answer to "may the engine act right now?". The scanner
consults :meth:`SessionManager.signals_allowed`; Phase-2 execution consults the
same gate plus force-close timing. The gate is absolute — no scanning, signals,
or (Phase 2) execution outside 09:15–15:30 IST on an NSE trading day
(OWNER_BRIEF IB6/IB17, CLAUDE.md hard limits).

State timeline on a trading day (IST):
    < 09:00              CLOSED    (pre-market, engine idle)
    09:00 – 09:15        PRE_OPEN  (bootstrap: historical data, reference levels)
    09:15 – 15:20        OPEN      (scan + emit new signals)
    15:20 – 15:30        CLOSING   (no new signals; force-close sweep in Phase 2)
    >= 15:30             CLOSED

``INDIA_DEV_MODE`` forces OPEN so the pipeline can be exercised off-hours.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

import config
from src.session.holiday_manager import HolidayManager


class SessionState(StrEnum):
    CLOSED = "CLOSED"
    PRE_OPEN = "PRE_OPEN"
    OPEN = "OPEN"
    CLOSING = "CLOSING"


class SessionManager:
    """Computes session state and gating decisions from the IST clock."""

    def __init__(self, holidays: HolidayManager | None = None) -> None:
        self._holidays = holidays or HolidayManager()

    def _now(self, now: datetime | None) -> datetime:
        return now or datetime.now(config.IST)

    def is_trading_day(self, d: date) -> bool:
        return self._holidays.is_trading_day(d)

    def current_state(self, now: datetime | None = None) -> SessionState:
        if config.INDIA_DEV_MODE:
            return SessionState.OPEN
        moment = self._now(now)
        if not self.is_trading_day(moment.date()):
            return SessionState.CLOSED
        t = moment.time()
        if t < config.PREOPEN_START:
            return SessionState.CLOSED
        if t < config.MARKET_OPEN:
            return SessionState.PRE_OPEN
        if t < config.LAST_SIGNAL_TIME:
            return SessionState.OPEN
        if t < config.MARKET_CLOSE:
            return SessionState.CLOSING
        return SessionState.CLOSED

    def is_open(self, now: datetime | None = None) -> bool:
        return self.current_state(now) is SessionState.OPEN

    def is_closing(self, now: datetime | None = None) -> bool:
        return self.current_state(now) is SessionState.CLOSING

    def signals_allowed(self, now: datetime | None = None) -> bool:
        """New signals may only be emitted while OPEN (stops at LAST_SIGNAL_TIME)."""
        return self.is_open(now)

    def minutes_to_close(self, now: datetime | None = None) -> int:
        """Whole minutes until 15:30 IST close; 0 once at/after close."""
        moment = self._now(now)
        close_dt = moment.replace(
            hour=config.MARKET_CLOSE.hour,
            minute=config.MARKET_CLOSE.minute,
            second=0,
            microsecond=0,
        )
        remaining = (close_dt - moment).total_seconds() / 60
        return max(0, int(remaining))
