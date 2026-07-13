"""Signal outcome tracking — the owner-directed two-target trade plan.

The 30-day quality window (Phase 2 prerequisite) is unmeasurable without
outcomes, and "never fabricate signal performance numbers" demands they be
recorded mechanically. The monitor watches every emitted signal against
the live 5m candles already in the tick store.

Trade plan (Session 18, revises IB12): book ``TP1_EXIT_FRACTION`` of the
position at TP1, move the stop on the remainder to break-even (one
round-trip cost beyond entry when ``BE_COST_BUFFER`` — a scratch runner
nets ~0 after STT, not a hidden loss), and run the rest to TP2:

  SL_HIT      — stop touched before TP1 (full loss)
  TP1_HIT     — target touched (legacy single-target signals, tp2 == 0)
  TP1_BE      — TP1 banked, runner stopped at break-even
  TP2_HIT     — TP1 banked, runner reached the stretch target
  TP1_EXPIRED — TP1 banked, session ended with the runner still open
                (runner scored at the last close)
  EXPIRED     — session ended with neither TP1 nor SL touched;
                scored at the last close

Candles are walked in order. Ambiguity rules stay conservative: a candle
touching both SL and TP1 resolves SL_HIT (no intrabar sequence data), and
the runner's BE/TP2 race only starts on the candle AFTER the TP1 touch —
the touch candle's own low/high sequence is unknowable, and almost every
candle that reaches TP1 has also traded near entry.

Points/pct are the position-weighted blend of both legs, signed from the
subscriber's perspective. No orders are involved anywhere — this is pure
measurement (Phase 1).

Restart resilience: on boot the engine reloads today's unresolved signals
and resumes tracking; a banked TP1 survives restarts via the persisted
``tp1_touched_at`` (the caller drains :meth:`drain_tp1_marks` and writes
them through ``signal_store.mark_tp1_touched``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import config
from src.data.india_tick_store import IndiaTickStore
from src.signals.model import IndiaSignal
from src.utils import get_logger

logger = get_logger("trade_monitor")

OUTCOME_TP1 = "TP1_HIT"
OUTCOME_SL = "SL_HIT"
OUTCOME_EXPIRED = "EXPIRED"
OUTCOME_TP1_BE = "TP1_BE"
OUTCOME_TP2 = "TP2_HIT"
OUTCOME_TP1_EXPIRED = "TP1_EXPIRED"


@dataclass
class TrackedSignal:
    signal_id: str
    symbol: str
    direction: str
    entry: float
    sl: float
    tp1: float
    registered_at: datetime
    # Two-target plan (0.0 tp2 = legacy single-target signal).
    tp2: float = 0.0
    be_price: float = 0.0
    tp1_touched_at: datetime | None = None


@dataclass(frozen=True)
class SignalOutcome:
    signal_id: str
    outcome: str
    exit_price: float
    points: float
    # Signed % return — points as a fraction of entry. This is the only
    # cross-instrument-comparable result: +67 NIFTY points and +0.4 TATASTEEL
    # points are both ~+0.2% moves, but summing raw points across a 46-base
    # universe is meaningless (it just weights by price level). The app/ops
    # aggregate this, not `points`. For two-leg outcomes this is the
    # position-weighted blend of both legs; ``exit_price`` is the runner's
    # exit.
    pct: float
    resolved_at: datetime


def _points(direction: str, entry: float, exit_price: float) -> float:
    return exit_price - entry if direction == "LONG" else entry - exit_price


def _be_price(direction: str, entry: float) -> float:
    """Break-even stop for the runner leg — cost-covering when configured."""
    if not config.BE_COST_BUFFER:
        return entry
    cost = config.round_trip_cost_points(entry)
    return entry + cost if direction == "LONG" else entry - cost


class IndiaTradeMonitor:
    """Resolves emitted signals against tick-store candles (two-target plan)."""

    def __init__(self, tick_store: IndiaTickStore) -> None:
        self._tick = tick_store
        self._open: dict[str, TrackedSignal] = {}
        # TP1 touches awaiting persistence (drained by the main loop and
        # written to india_signals.tp1_touched_at so a banked TP1 survives
        # an engine restart).
        self._pending_tp1_marks: list[tuple[str, datetime]] = []

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
                tp2=s.tp2,
                be_price=_be_price(s.direction, s.entry),
                registered_at=now,
            )
            logger.info(
                "tracking {} {} {} entry={:.1f} sl={:.1f} tp1={:.1f} tp2={:.1f}",
                s.signal_id,
                s.direction,
                s.symbol,
                s.entry,
                s.sl,
                s.tp1,
                s.tp2,
            )

    def resume(self, rows: list[dict], now: datetime) -> None:
        """Re-track today's unresolved signals after an engine restart."""
        for row in rows:
            signal_id = str(row.get("signal_id", ""))
            if not signal_id or signal_id in self._open:
                continue
            registered = self._parse_ts(str(row.get("created_at", "")), now)
            touched = None
            raw_touched = row.get("tp1_touched_at")
            if raw_touched:
                touched = self._parse_ts(str(raw_touched), now)
            direction = str(row.get("direction", ""))
            entry = float(row.get("entry", 0) or 0)
            self._open[signal_id] = TrackedSignal(
                signal_id=signal_id,
                symbol=str(row.get("symbol", "")),
                direction=direction,
                entry=entry,
                sl=float(row.get("sl", 0) or 0),
                tp1=float(row.get("tp1", 0) or 0),
                tp2=float(row.get("tp2", 0) or 0),
                be_price=_be_price(direction, entry),
                registered_at=registered,
                tp1_touched_at=touched,
            )
        if rows:
            logger.info("resumed tracking {} unresolved signals", self.open_count)

    @staticmethod
    def _parse_ts(raw: str, fallback: datetime) -> datetime:
        if raw:
            try:
                parsed = datetime.fromisoformat(raw)
                if parsed.tzinfo:
                    return parsed
                localized: datetime = fallback.tzinfo.localize(  # type: ignore[union-attr]
                    parsed
                )
                return localized
            except ValueError:
                pass
        return fallback

    def drain_tp1_marks(self) -> list[tuple[str, datetime]]:
        """TP1 touches since the last drain — the caller persists them."""
        marks = self._pending_tp1_marks
        self._pending_tp1_marks = []
        return marks

    def check(self, now: datetime) -> list[SignalOutcome]:
        """Walk candles in order and resolve every decided signal."""
        resolved: list[SignalOutcome] = []
        for tracked in list(self._open.values()):
            candles = [
                c
                for c in self._tick.get_candles_5m(tracked.symbol)
                if c.ts >= tracked.registered_at
            ]
            outcome = self._walk(tracked, candles, now)
            if outcome is not None:
                resolved.append(outcome)
        return resolved

    def _walk(
        self, tracked: TrackedSignal, candles: list, now: datetime
    ) -> SignalOutcome | None:
        is_long = tracked.direction == "LONG"
        for c in candles:
            if tracked.tp1_touched_at is None:
                sl_touch = c.low <= tracked.sl if is_long else c.high >= tracked.sl
                tp_touch = c.high >= tracked.tp1 if is_long else c.low <= tracked.tp1
                # Same-candle tie resolves to SL (conservative — module doc).
                if sl_touch:
                    return self._close(tracked, OUTCOME_SL, tracked.sl, now)
                if tp_touch:
                    if tracked.tp2 <= 0:
                        # Legacy single-target signal — behaviour unchanged.
                        return self._close(tracked, OUTCOME_TP1, tracked.tp1, now)
                    tracked.tp1_touched_at = c.ts
                    self._pending_tp1_marks.append((tracked.signal_id, c.ts))
                    logger.info(
                        "TP1 banked on {} — runner to tp2={:.1f} behind be={:.1f}",
                        tracked.signal_id,
                        tracked.tp2,
                        tracked.be_price,
                    )
                continue
            # Runner leg: only candles after the TP1-touch candle count (the
            # touch candle's intrabar sequence is unknowable — conservative).
            if c.ts <= tracked.tp1_touched_at:
                continue
            be_touch = (
                c.low <= tracked.be_price if is_long else c.high >= tracked.be_price
            )
            tp2_touch = c.high >= tracked.tp2 if is_long else c.low <= tracked.tp2
            # Same-candle tie resolves to BE (adverse first, conservative).
            if be_touch:
                return self._close(tracked, OUTCOME_TP1_BE, tracked.be_price, now)
            if tp2_touch:
                return self._close(tracked, OUTCOME_TP2, tracked.tp2, now)
        return None

    def clear(self) -> int:
        """Owner maintenance (ops Control panel): drop all tracked signals
        without recording outcomes — used when the signal history itself is
        being wiped, so the monitor doesn't resolve ghosts into an empty
        table. Returns how many were dropped."""
        dropped = len(self._open)
        self._open.clear()
        self._pending_tp1_marks.clear()
        if dropped:
            logger.info("trade monitor cleared — {} tracked signals dropped", dropped)
        return dropped

    def force_close_all(self, now: datetime) -> list[SignalOutcome]:
        """Session over — score everything still open at its last close."""
        resolved: list[SignalOutcome] = []
        for tracked in list(self._open.values()):
            candles = self._tick.get_candles_5m(tracked.symbol)
            exit_price = candles[-1].close if candles else tracked.entry
            outcome = (
                OUTCOME_TP1_EXPIRED
                if tracked.tp1_touched_at is not None
                else OUTCOME_EXPIRED
            )
            resolved.append(self._close(tracked, outcome, exit_price, now))
        return resolved

    def _close(
        self,
        tracked: TrackedSignal,
        outcome: str,
        exit_price: float,
        now: datetime,
    ) -> SignalOutcome:
        del self._open[tracked.signal_id]
        if outcome in (OUTCOME_TP1_BE, OUTCOME_TP2, OUTCOME_TP1_EXPIRED):
            # Position-weighted blend: TP1 leg banked, runner leg at exit_price.
            frac = min(1.0, max(0.0, config.TP1_EXIT_FRACTION))
            points = frac * _points(
                tracked.direction, tracked.entry, tracked.tp1
            ) + (1.0 - frac) * _points(tracked.direction, tracked.entry, exit_price)
        else:
            points = _points(tracked.direction, tracked.entry, exit_price)
        pct = (points / tracked.entry * 100.0) if tracked.entry > 0 else 0.0
        result = SignalOutcome(
            signal_id=tracked.signal_id,
            outcome=outcome,
            exit_price=exit_price,
            points=points,
            pct=pct,
            resolved_at=now,
        )
        logger.info(
            "outcome {} -> {} exit={:.1f} points={:+.1f} ({:+.2f}%)",
            tracked.signal_id,
            outcome,
            exit_price,
            result.points,
            result.pct,
        )
        return result
