"""Per-stock earnings blackout calendar (G8 — single-stock event risk).

A single-stock F&O signal fired into its own results window is a coin-flip on
the print, not a read on structure: the Q1-2026 window bled exactly here
(INFY/TCS/HUL/NESTLE reporting; INFY 0% win in the ops audit). Index bases
have no single earnings date and are never blacked out.

Same file discipline as the NSE holiday and macro-event calendars: the calendar
is loaded once at construction (zero I/O on the scan path), and an
``verified: false`` / empty file logs a loud warning so a stale or unpopulated
calendar is never a silent gap. Point ``INDIA_EARNINGS_EVENTS_FILE`` (or the
config default) at a maintained NSE results-calendar export; unavailable →
no blackout, never fabricated.

File shape::

    {
      "verified": true,
      "earnings": {
        "INFY":   ["2026-07-16"],
        "TCS":    ["2026-07-09"],
        "RELIANCE": ["2026-07-18", "2026-10-17"]
      }
    }

Dates are the announcement (results) dates in IST. The blackout window is
``[date - DAYS_BEFORE, date + DAYS_AFTER]`` inclusive — the pre-print drift and
the post-print gap are both un-tradeable on a 5m scalp.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import config
from src.utils import get_logger

log = get_logger(__name__)


def _coerce(d: date | str) -> date:
    return d if isinstance(d, date) else date.fromisoformat(d)


class EarningsCalendar:
    """Answers "is base B in its earnings blackout window on date D?"."""

    def __init__(
        self,
        path: str | None = None,
        *,
        days_before: int | None = None,
        days_after: int | None = None,
    ) -> None:
        self._path = Path(path or config.EARNINGS_EVENTS_FILE)
        self._days_before = (
            config.EARNINGS_BLACKOUT_DAYS_BEFORE
            if days_before is None
            else days_before
        )
        self._days_after = (
            config.EARNINGS_BLACKOUT_DAYS_AFTER
            if days_after is None
            else days_after
        )
        self._earnings, self._verified = self._load()
        if not self._verified or not self._earnings:
            log.warning(
                "earnings calendar at {} is UNVERIFIED or empty — single-stock "
                "earnings blackout (G8) is INERT until it is populated and "
                "verified=true. Point INDIA_EARNINGS_EVENTS_FILE at a live NSE "
                "results calendar.",
                self._path,
            )

    def _load(self) -> tuple[dict[str, set[date]], bool]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            log.warning("earnings calendar missing at {}", self._path)
            return {}, False
        except (ValueError, OSError) as exc:  # malformed JSON / read error
            log.warning("earnings calendar unreadable at {}: {}", self._path, exc)
            return {}, False
        earnings: dict[str, set[date]] = {}
        for base, days in (raw.get("earnings") or {}).items():
            key = str(base).strip().upper()
            if not key:
                continue
            parsed: set[date] = set()
            for d in days or []:
                try:
                    parsed.add(date.fromisoformat(str(d)))
                except ValueError:
                    log.warning(
                        "earnings calendar: bad date {!r} for {} — skipped", d, key
                    )
            if parsed:
                earnings[key] = parsed
        return earnings, bool(raw.get("verified", False))

    def is_verified(self) -> bool:
        return self._verified

    def earnings_on(self, base: str, d: date | str) -> date | None:
        """The results date whose blackout window contains ``d`` for ``base``,
        or ``None`` if ``base`` is clear to trade on ``d``."""
        days = self._earnings.get(str(base).strip().upper())
        if not days:
            return None
        target = _coerce(d)
        for ed in days:
            if (ed - timedelta(days=self._days_before)) <= target <= (
                ed + timedelta(days=self._days_after)
            ):
                return ed
        return None
