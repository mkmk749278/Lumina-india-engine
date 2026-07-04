"""Signal outcome tracking — did each emitted signal hit TP1, SL, or expire?

The 30-day quality window (Phase 2 prerequisite) is unmeasurable without
outcomes, and "never fabricate signal performance numbers" demands they be
recorded mechanically. The monitor watches every emitted signal against
the live 5m candles already in the tick store and resolves each one to:

  TP1_HIT — target touched first
  SL_HIT  — stop touched first (if both are touched within the same 5m
            candle, the tie resolves to SL_HIT: with no intrabar sequence
            data the honest choice is the conservative one)
  EXPIRED — session ended with neither touched; scored at the last close

Points are signed from the subscriber's perspective (LONG: exit − entry;
SHORT: entry − exit). No orders are involved anywhere — this is pure
measurement (Phase 1).

Restart resilience: on boot the engine reloads today's unresolved signals
and resumes tracking. Candles from before the restart are still in the
seeded history, so touches during the gap are not lost.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from src.data.india_tick_store import IndiaTickStore
from src.signals.model import IndiaSignal
from src.utils import get_logger

logger = get_logger("trade_monitor")

OUTCOME_TP1 = "TP1_HIT"
OUTCOME_SL = "SL_HIT"
OUTCOME_EXPIRED = "EXPIRED"


@dataclass(frozen=True)
class TrackedSignal:
    signal_id: str
    symbol: str
    direction: str
    entry: float
    sl: float
    tp1: float
    registered_at: datetime


@dataclass(frozen=True)
class SignalOutcome:
    signal_id: str
    outcome: str
    exit_price: float
    points: float
    resolved_at: datetime


def _points(direction: str, entry: float, exit_price: float) -> float:
    return exit_price - entry if direction == "LONG" else entry - exit_price


class IndiaTradeMonitor:
    """Resolves emitted signals to TP1/SL/EXPIRED against tick-store candles."""

    def __init__(self, tick_store: IndiaTickStore) -> None:
        self._tick = tick_store
        self._open: dict[str, TrackedSignal] = {}

    @property
    def open_count(self) -> int:
        return len(self._open)

    def register(self, signals: list[IndiaSignal], now: datetime) -> None:
        for s in signals:
            if s.signal_id in self._open:
                continue
            self._open[s.signal_id] = TrackedSignal(
                signal_id=s.signal_id,
                symbol=s.symbol,
                direction=s.direction,
                entry=s.entry,
                sl=s.sl,
                tp1=s.tp1,
                registered_at=now,
            )
            logger.info(
                "tracking {} {} {} entry={:.1f} sl={:.1f} tp1={:.1f}",
                s.signal_id,
                s.direction,
                s.symbol,
                s.entry,
                s.sl,
                s.tp1,
            )

    def resume(self, rows: list[dict], now: datetime) -> None:
        """Re-track today's unresolved signals after an engine restart."""
        for row in rows:
            signal_id = str(row.get("signal_id", ""))
            if not signal_id or signal_id in self._open:
                continue
            registered = now
            raw_created = str(row.get("created_at", ""))
            if raw_created:
                try:
                    parsed = datetime.fromisoformat(raw_created)
                    registered = (
                        parsed
                        if parsed.tzinfo
                        else now.tzinfo.localize(parsed)  # type: ignore[union-attr]
                    )
                except ValueError:
                    pass
            self._open[signal_id] = TrackedSignal(
                signal_id=signal_id,
                symbol=str(row.get("symbol", "")),
                direction=str(row.get("direction", "")),
                entry=float(row.get("entry", 0) or 0),
                sl=float(row.get("sl", 0) or 0),
                tp1=float(row.get("tp1", 0) or 0),
                registered_at=registered,
            )
        if rows:
            logger.info("resumed tracking {} unresolved signals", self.open_count)

    def check(self, now: datetime) -> list[SignalOutcome]:
        """Resolve any tracked signal whose SL or TP1 has been touched."""
        resolved: list[SignalOutcome] = []
        for tracked in list(self._open.values()):
            candles = [
                c
                for c in self._tick.get_candles_5m(tracked.symbol)
                if c.ts >= tracked.registered_at
            ]
            if not candles:
                continue

            if tracked.direction == "LONG":
                sl_touched = any(c.low <= tracked.sl for c in candles)
                tp_touched = any(c.high >= tracked.tp1 for c in candles)
            else:
                sl_touched = any(c.high >= tracked.sl for c in candles)
                tp_touched = any(c.low <= tracked.tp1 for c in candles)

            if not sl_touched and not tp_touched:
                continue

            # Same-candle tie resolves to SL (conservative — see module doc).
            if sl_touched:
                outcome, exit_price = OUTCOME_SL, tracked.sl
            else:
                outcome, exit_price = OUTCOME_TP1, tracked.tp1

            resolved.append(self._close(tracked, outcome, exit_price, now))
        return resolved

    def force_close_all(self, now: datetime) -> list[SignalOutcome]:
        """Session over — score everything still open at its last close."""
        resolved: list[SignalOutcome] = []
        for tracked in list(self._open.values()):
            candles = self._tick.get_candles_5m(tracked.symbol)
            exit_price = candles[-1].close if candles else tracked.entry
            resolved.append(
                self._close(tracked, OUTCOME_EXPIRED, exit_price, now)
            )
        return resolved

    def _close(
        self,
        tracked: TrackedSignal,
        outcome: str,
        exit_price: float,
        now: datetime,
    ) -> SignalOutcome:
        del self._open[tracked.signal_id]
        result = SignalOutcome(
            signal_id=tracked.signal_id,
            outcome=outcome,
            exit_price=exit_price,
            points=_points(tracked.direction, tracked.entry, exit_price),
            resolved_at=now,
        )
        logger.info(
            "outcome {} -> {} exit={:.1f} points={:+.1f}",
            tracked.signal_id,
            outcome,
            exit_price,
            result.points,
        )
        return result
