"""Signal outcome tracking — the owner-directed two-target trade plan.

The 30-day quality window (Phase 2 prerequisite) is unmeasurable without
outcomes, and "never fabricate signal performance numbers" demands they be
recorded mechanically. The monitor watches every emitted signal against
the candles already in the tick store.

Trade plan (Session 18, revises IB12): book ``TP1_EXIT_FRACTION`` of the
position at TP1, move the stop on the remainder to break-even (one
round-trip cost beyond entry when ``BE_COST_BUFFER`` — a scratch runner
nets ~0 after STT, not a hidden loss), and run the rest to TP2:

  NOT_TRIGGERED — LEVEL entry never traded through (no fill, no trade;
                  excluded from win/EV denominators everywhere)
  SL_HIT      — stop touched before TP1 (full loss)
  TP1_HIT     — target touched (legacy single-target signals, tp2 == 0)
  TP1_BE      — TP1 banked, runner stopped at break-even
  TP2_HIT     — TP1 banked, runner reached the stretch target
  TP1_EXPIRED — TP1 banked, session ended with the runner still open
                (runner scored at the last close)
  EXPIRED     — session ended with neither TP1 nor SL touched;
                scored at the last close

Entry-trigger state machine (Session 21 — the ledger-truth fix): a signal
whose printed entry is a resting LEVEL (ORB / VSB / BDS) is PENDING until a
candle actually trades through the entry price. Before this, every signal
was assumed filled at its printed entry at emission — for breakout setups
that entry is systematically better than the market at emission, so the
ledger credited fills nobody could have had. A pending signal is cancelled
(NOT_TRIGGERED) when the level goes untouched for
``ENTRY_TRIGGER_EXPIRY_MIN``, when SL or TP1 is reached before the entry
ever fills, or at session close. MARKET signals fill at registration —
exactly the legacy behaviour.

Resolution walks ``OUTCOME_RESOLUTION_TF`` candles (default 1m — the live
window's median SL distance sits INSIDE one 5m bar's range, so the 5m
walk's conservative same-candle tie was manufacturing SL_HITs), falling
back to 5m per signal when 1m coverage doesn't reach its registration
(mid-session restart: the 1m buffer builds from live ticks only). Each
outcome records which timeframe resolved it and whether the resolving
candle was an ambiguous both-levels tie.

Ambiguity rules stay conservative: a candle touching both SL and TP1
resolves SL_HIT (no intrabar sequence data), and the runner's BE/TP2 race
only starts on the candle AFTER the TP1 touch — the touch candle's own
low/high sequence is unknowable, and almost every candle that reaches TP1
has also traded near entry. The same convention applies to the trigger
candle: only the adverse (SL) check runs on it; the TP race starts on the
next candle.

Points/pct are the position-weighted blend of both legs, signed from the
subscriber's perspective. MFE/MAE (max favourable / adverse excursion, %
of entry, post-trigger) are recorded per outcome — the future basis for
stop/target geometry. No orders are involved anywhere — this is pure
measurement (Phase 1).

Restart resilience: on boot the engine reloads today's unresolved signals
and resumes tracking; a banked TP1 and a fired trigger survive restarts via
the persisted ``tp1_touched_at`` / ``triggered_at`` (the caller drains
:meth:`drain_tp1_marks` / :meth:`drain_trigger_marks` and writes them
through ``signal_store``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import config
from src.data.india_tick_store import IndiaTickStore
from src.market.candle import Candle
from src.signals.model import EntryType, IndiaSignal
from src.utils import get_logger

logger = get_logger("trade_monitor")

OUTCOME_TP1 = "TP1_HIT"
OUTCOME_SL = "SL_HIT"
OUTCOME_EXPIRED = "EXPIRED"
OUTCOME_TP1_BE = "TP1_BE"
OUTCOME_TP2 = "TP2_HIT"
OUTCOME_TP1_EXPIRED = "TP1_EXPIRED"
OUTCOME_NOT_TRIGGERED = "NOT_TRIGGERED"

# Outcomes that represent a filled trade (everything except a cancelled,
# never-filled LEVEL entry). NOT_TRIGGERED rows carry pct 0 and are excluded
# from every win/EV denominator downstream.
FILLED_OUTCOMES = frozenset(
    {
        OUTCOME_TP1,
        OUTCOME_SL,
        OUTCOME_EXPIRED,
        OUTCOME_TP1_BE,
        OUTCOME_TP2,
        OUTCOME_TP1_EXPIRED,
    }
)


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
    # Entry-trigger state (Session 21). MARKET signals are triggered at
    # registration; LEVEL signals stay pending until a candle trades through
    # the entry. None = pending.
    entry_type: str = EntryType.MARKET
    triggered_at: datetime | None = None
    # Walk telemetry — recomputed on every check() from the full candle
    # window (the walk is a stateless re-walk), final values persisted with
    # the outcome. mfe/mae are % of entry, post-trigger, favourable/adverse.
    mfe_pct: float = 0.0
    mae_pct: float = 0.0
    bars_walked: int = 0
    resolution_tf: str = "5m"
    ambiguous_tie: bool = False


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
    # Truth telemetry (Session 21).
    mfe_pct: float = 0.0
    mae_pct: float = 0.0
    bars_to_resolve: int = 0
    resolution_tf: str = "5m"
    ambiguous_tie: bool = False


def _points(direction: str, entry: float, exit_price: float) -> float:
    return exit_price - entry if direction == "LONG" else entry - exit_price


def _be_price(direction: str, entry: float) -> float:
    """Break-even stop for the runner leg — cost-covering when configured."""
    if not config.BE_COST_BUFFER:
        return entry
    cost = config.round_trip_cost_points(entry)
    return entry + cost if direction == "LONG" else entry - cost


def _trigger_pending(tracked: TrackedSignal) -> bool:
    """True while a LEVEL entry is still waiting for its fill."""
    return (
        config.ENTRY_TRIGGER_ENABLED
        and tracked.entry_type == EntryType.LEVEL
        and tracked.triggered_at is None
    )


def walk_signal(
    tracked: TrackedSignal, candles: list
) -> tuple[str, float] | None:
    """Walk *candles* (ascending, all >= registered_at) and decide *tracked*.

    Pure decision logic shared verbatim by the live monitor and the replay
    harness (tools/replay.py) — one implementation, one semantics. Mutates
    ``tracked``'s trigger / TP1-touch / telemetry state; returns
    ``(outcome, exit_price)`` once decided, else None (still open).

    The walk is a stateless re-walk: telemetry (mfe/mae/bars/ambiguity) is
    reset and recomputed from the full window every call, so repeated calls
    with a growing candle list stay consistent.
    """
    is_long = tracked.direction == "LONG"
    is_level = (
        config.ENTRY_TRIGGER_ENABLED and tracked.entry_type == EntryType.LEVEL
    )
    tracked.mfe_pct = 0.0
    tracked.mae_pct = 0.0
    tracked.bars_walked = 0
    tracked.ambiguous_tie = False
    trigger_deadline = tracked.registered_at + timedelta(
        minutes=max(1, config.ENTRY_TRIGGER_EXPIRY_MIN)
    )

    for c in candles:
        if _trigger_pending(tracked):
            touched = c.low <= tracked.entry <= c.high
            if not touched:
                # The setup can die before the fill: TP1 reached = the move
                # ran without giving the entry; SL reached = the idea failed
                # before it filled; deadline passed = a retest that hasn't
                # come in ENTRY_TRIGGER_EXPIRY_MIN is a different market.
                sl_touch = c.low <= tracked.sl if is_long else c.high >= tracked.sl
                tp_touch = c.high >= tracked.tp1 if is_long else c.low <= tracked.tp1
                if sl_touch or tp_touch or c.ts >= trigger_deadline:
                    return OUTCOME_NOT_TRIGGERED, 0.0
                continue
            tracked.triggered_at = c.ts
            # falls through to the trigger-candle handling below

        # A LEVEL signal ignores everything strictly before its fill, and on
        # the fill candle itself only the adverse (SL) check runs — its
        # intrabar sequence is unknowable, and a candle that spans entry has
        # usually spanned much of the range beyond it too. Keyed off the
        # PERSISTED triggered_at so a re-walk (the monitor re-walks the full
        # window every check) and a post-restart resume replay identically.
        if is_level and tracked.triggered_at is not None:
            if c.ts < tracked.triggered_at:
                continue
            if c.ts == tracked.triggered_at:
                tracked.bars_walked += 1
                _update_excursions(tracked, c, is_long)
                sl_touch = (
                    c.low <= tracked.sl if is_long else c.high >= tracked.sl
                )
                if sl_touch:
                    tracked.ambiguous_tie = True
                    return OUTCOME_SL, tracked.sl
                continue

        tracked.bars_walked += 1
        _update_excursions(tracked, c, is_long)

        if tracked.tp1_touched_at is None:
            sl_touch = c.low <= tracked.sl if is_long else c.high >= tracked.sl
            tp_touch = c.high >= tracked.tp1 if is_long else c.low <= tracked.tp1
            # Same-candle tie resolves to SL (conservative — module doc).
            if sl_touch:
                if tp_touch:
                    tracked.ambiguous_tie = True
                return OUTCOME_SL, tracked.sl
            if tp_touch:
                if tracked.tp2 <= 0:
                    # Legacy single-target signal — behaviour unchanged.
                    return OUTCOME_TP1, tracked.tp1
                tracked.tp1_touched_at = c.ts
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
            if tp2_touch:
                tracked.ambiguous_tie = True
            return OUTCOME_TP1_BE, tracked.be_price
        if tp2_touch:
            return OUTCOME_TP2, tracked.tp2

    # Candle list exhausted with the trigger still pending: enforce the
    # wall-clock deadline even when no candle has printed past it (thin
    # tape) — the caller passes candles up to "now", so a stale pending
    # signal is cancelled on the next check after the deadline.
    return None


def _update_excursions(tracked: TrackedSignal, c: Candle, is_long: bool) -> None:
    if tracked.entry <= 0:
        return
    if is_long:
        favourable = (c.high - tracked.entry) / tracked.entry * 100.0
        adverse = (tracked.entry - c.low) / tracked.entry * 100.0
    else:
        favourable = (tracked.entry - c.low) / tracked.entry * 100.0
        adverse = (c.high - tracked.entry) / tracked.entry * 100.0
    tracked.mfe_pct = max(tracked.mfe_pct, round(favourable, 4))
    tracked.mae_pct = max(tracked.mae_pct, round(adverse, 4))


class IndiaTradeMonitor:
    """Resolves emitted signals against tick-store candles (two-target plan)."""

    def __init__(self, tick_store: IndiaTickStore) -> None:
        self._tick = tick_store
        self._open: dict[str, TrackedSignal] = {}
        # TP1 touches / entry triggers awaiting persistence (drained by the
        # main loop and written through signal_store so both survive an
        # engine restart).
        self._pending_tp1_marks: list[tuple[str, datetime]] = []
        self._pending_trigger_marks: list[tuple[str, datetime]] = []

    @property
    def open_count(self) -> int:
        return len(self._open)

    def register(self, signals: list[IndiaSignal], now: datetime) -> None:
        for s in signals:
            if s.signal_id in self._open:
                continue
            is_level = (
                config.ENTRY_TRIGGER_ENABLED
                and s.entry_type == EntryType.LEVEL
            )
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
                entry_type=s.entry_type,
                triggered_at=None if is_level else now,
            )
            logger.info(
                "tracking {} {} {} entry={:.1f} ({}) sl={:.1f} tp1={:.1f} tp2={:.1f}",
                s.signal_id,
                s.direction,
                s.symbol,
                s.entry,
                s.entry_type,
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
            entry_type = str(row.get("entry_type") or EntryType.MARKET)
            triggered = None
            raw_triggered = row.get("triggered_at")
            if raw_triggered:
                triggered = self._parse_ts(str(raw_triggered), now)
            elif not (
                config.ENTRY_TRIGGER_ENABLED and entry_type == EntryType.LEVEL
            ):
                triggered = registered
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
                entry_type=entry_type,
                triggered_at=triggered,
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

    def drain_trigger_marks(self) -> list[tuple[str, datetime]]:
        """Entry triggers since the last drain — the caller persists them."""
        marks = self._pending_trigger_marks
        self._pending_trigger_marks = []
        return marks

    def _candles_for(self, tracked: TrackedSignal) -> list:
        """The candle window to walk for *tracked*, on the configured
        resolution timeframe with a per-signal 5m fallback.

        1m is only trusted when its coverage reaches back to (within 60s of)
        the signal's registration — after a mid-session restart the 1m ring
        buffer starts empty (it builds from live ticks; the REST seed has no
        1m), and walking a gappy window would silently skip the candles that
        actually decided the trade.
        """
        tf = config.OUTCOME_RESOLUTION_TF
        if tf != "5m":
            fine = [
                c
                for c in self._tick.get_candles(tracked.symbol, tf)
                if c.ts >= tracked.registered_at
            ]
            all_fine = self._tick.get_candles(tracked.symbol, tf)
            covered = bool(all_fine) and (
                all_fine[0].ts
                <= tracked.registered_at + timedelta(seconds=60)
            )
            if fine and covered:
                tracked.resolution_tf = tf
                return fine
        tracked.resolution_tf = "5m"
        return [
            c
            for c in self._tick.get_candles_5m(tracked.symbol)
            if c.ts >= tracked.registered_at
        ]

    def check(self, now: datetime) -> list[SignalOutcome]:
        """Walk candles in order and resolve every decided signal."""
        resolved: list[SignalOutcome] = []
        for tracked in list(self._open.values()):
            had_tp1 = tracked.tp1_touched_at
            had_trigger = tracked.triggered_at
            decision = walk_signal(tracked, self._candles_for(tracked))
            if tracked.triggered_at is not None and had_trigger is None:
                self._pending_trigger_marks.append(
                    (tracked.signal_id, tracked.triggered_at)
                )
            if tracked.tp1_touched_at is not None and had_tp1 is None:
                self._pending_tp1_marks.append(
                    (tracked.signal_id, tracked.tp1_touched_at)
                )
                logger.info(
                    "TP1 banked on {} — runner to tp2={:.1f} behind be={:.1f}",
                    tracked.signal_id,
                    tracked.tp2,
                    tracked.be_price,
                )
            if decision is None:
                # Wall-clock trigger expiry: no candle has printed past the
                # deadline (thin tape / feed gap) but the deadline is real.
                if _trigger_pending(tracked) and now >= (
                    tracked.registered_at
                    + timedelta(
                        minutes=max(1, config.ENTRY_TRIGGER_EXPIRY_MIN)
                    )
                ):
                    resolved.append(
                        self._close(tracked, OUTCOME_NOT_TRIGGERED, 0.0, now)
                    )
                continue
            outcome, exit_price = decision
            resolved.append(self._close(tracked, outcome, exit_price, now))
        return resolved

    def clear(self) -> int:
        """Owner maintenance (ops Control panel): drop all tracked signals
        without recording outcomes — used when the signal history itself is
        being wiped, so the monitor doesn't resolve ghosts into an empty
        table. Returns how many were dropped."""
        dropped = len(self._open)
        self._open.clear()
        self._pending_tp1_marks.clear()
        self._pending_trigger_marks.clear()
        if dropped:
            logger.info("trade monitor cleared — {} tracked signals dropped", dropped)
        return dropped

    def force_close_all(self, now: datetime) -> list[SignalOutcome]:
        """Session over — score everything still open at its last close."""
        resolved: list[SignalOutcome] = []
        for tracked in list(self._open.values()):
            if _trigger_pending(tracked):
                # Never filled — the session ending doesn't invent a trade.
                resolved.append(
                    self._close(tracked, OUTCOME_NOT_TRIGGERED, 0.0, now)
                )
                continue
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
        if outcome == OUTCOME_NOT_TRIGGERED:
            # No fill, no trade — zero result by construction, and excluded
            # from every win/EV denominator downstream.
            points = 0.0
        elif outcome in (OUTCOME_TP1_BE, OUTCOME_TP2, OUTCOME_TP1_EXPIRED):
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
            mfe_pct=tracked.mfe_pct,
            mae_pct=tracked.mae_pct,
            bars_to_resolve=tracked.bars_walked,
            resolution_tf=tracked.resolution_tf,
            ambiguous_tie=tracked.ambiguous_tie,
        )
        logger.info(
            "outcome {} -> {} exit={:.1f} points={:+.1f} ({:+.2f}%) [{} {}bars]",
            tracked.signal_id,
            outcome,
            exit_price,
            result.points,
            result.pct,
            result.resolution_tf,
            result.bars_to_resolve,
        )
        return result
