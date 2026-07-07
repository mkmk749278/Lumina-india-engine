"""Macro binary-event calendar (OWNER_BRIEF IB13).

RBI MPC announcement days, Union Budget, and any other scheduled binary macro
event are no-signal days: better to miss a session than emit into a 10:00 IST
policy shock. The event-risk gate consults this calendar once per candidate —
the file is loaded once at construction (zero I/O on the scan path).

Same file discipline as the NSE holiday calendar: ``verified: false`` logs a
loud warning so an empty/stale calendar is never a silent gap.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import config
from src.utils import get_logger

log = get_logger(__name__)


def _coerce(d: date | str) -> date:
    return d if isinstance(d, date) else date.fromisoformat(d)


class EventCalendar:
    """Loads and answers "is date D a scheduled macro binary-event day?"."""

    def __init__(self, path: str | None = None) -> None:
        self._path = Path(path or config.MACRO_EVENTS_FILE)
        self._events, self._verified = self._load()
        if not self._verified:
            log.warning(
                "macro event calendar at {} is UNVERIFIED — populate the RBI "
                "MPC / Budget dates and set verified=true.",
                self._path,
            )

    def _load(self) -> tuple[dict[date, str], bool]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            log.warning("macro event calendar missing at {}", self._path)
            return {}, False
        events = {
            date.fromisoformat(k): str(v)
            for k, v in (raw.get("events") or {}).items()
        }
        return events, bool(raw.get("verified", False))

    def is_verified(self) -> bool:
        return self._verified

    def event_on(self, d: date | str) -> str | None:
        """The event label for date ``d``, or ``None`` if it is event-free."""
        return self._events.get(_coerce(d))
