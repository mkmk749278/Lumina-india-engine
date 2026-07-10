"""India scanner — 30s scan loop + gate chain + scoring + ranked emission.

Runs every ``SCAN_INTERVAL_SEC`` during market hours. For each allowed
base, builds an ``IndiaContext``, runs all enabled evaluators, gates
candidates, scores survivors, then emits highest-confidence-first under
the per-scan / per-day / per-direction caps.

Contexts for the whole universe are built first, the index contexts anchor a
per-base ``index_bias`` (src/dependency.py), then evaluators run per base.

Pre-score gates (spec §9):
  1. session_gate — market hours?
  2. warmup_gate — past the 09:30 IST warm-up (opening auction noise)?
  3. stale_data_gate — live ticks actually flowing for this symbol?
     (a frozen buffer produces unfillable entries — live 2026-07-10)
  4. spread_gate — bid-ask too wide? (Phase 2 — always pass in Phase 1)
  5. cooldown_gate — evaluator fired recently?
  6. event_risk_gate — VIX > 25 or scheduled macro event day (IB13)?
  7. circuit_check_gate — extreme intraday move?
  8. min_atr_gate — ATR above tradeable threshold?
  9. sl_noise_gate — stop wider than bar noise (>= 0.45x ATR)?
 10. min_scalp_gate — TP1 distance clears the IB11 STT-viable minimum?
 11. oi_liquidity_gate — enough open interest?
 12. index_conflict_gate — stock signal not fighting its proxy index's
     intraday bias (dependency pairs)?

Post-score:
 12. confidence_floor_gate — score >= emit floor (+5 on expiry day, IB16)?

Emission stage (highest confidence first across the whole scan):
 13. duplicate_direction_gate — same-direction cap per base per day
 14. direction_conflict_gate — no opposite-direction signal on a base
     minutes after one was emitted (whipsawing subscribers is worse
     than missing a genuine V-reversal)
 15. correlation_group_gate — same-direction cap per sector group per
     scan: one index move validates several near-identical correlated
     setups at once; subscribers get the best one, not three copies
 16. setup_flood_gate — same-direction cap per setup class per scan:
     one market-wide move fires the same evaluator across many groups
 17. scan_cap_gate — per-scan flood limiter (best few per scan)
 18. daily_cap_gate — optional hard daily ceiling; OFF by default
     (INDIA_MAX_SIGNALS_PER_DAY=0 — owner decision: no fixed daily
     signal budget, volume is bounded by quality gates, not a count)

Restart safety: the chain's day-cumulative state (daily caps, cooldowns,
per-base last emission) is rehydrated from ``india_signals`` at boot —
a mid-session deploy no longer re-opens the daily budget.

Every gate rejection is logged with gate name + reason (suppression
telemetry — CLAUDE.md). Surface via ``/api/india/suppressed`` and ops.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta

import config
from src import dependency
from src.channels import (
    BreakdownShort,
    DivergenceContinuation,
    Evaluator,
    ExpiryGammaSqueeze,
    FailedAuctionReclaim,
    IndiaVixExtreme,
    LiquiditySweepReversal,
    MaCrossTrendShift,
    OiSpikeReversal,
    OpeningRangeBreakout,
    PcrExtreme,
    QuietCompressionBreak,
    SrFlipRetest,
    TrendPullbackEma,
    VolumeSurgeBreakout,
)
from src.data.india_context_builder import IndiaContextBuilder
from src.session.event_calendar import EventCalendar
from src.session.expiry_manager import ExpiryManager
from src.session.session_manager import SessionManager, SessionState
from src.signal_quality import IndiaSignalScoringEngine, tier_for
from src.signals.model import IndiaContext, IndiaSignal
from src.signals.model import Tier as Tier
from src.utils import get_logger

logger = get_logger("scanner")

SCAN_INTERVAL_SEC: int = config._safe_int("INDIA_SCAN_INTERVAL", 30)

_VIX_EVENT_THRESHOLD: float = config._safe_float(
    "INDIA_VIX_EVENT_THRESHOLD", 25.0
)
_CIRCUIT_MOVE_PCT: float = config._safe_float(
    "INDIA_CIRCUIT_MOVE_PCT", 5.0
)
_COOLDOWN_SEC: int = config._safe_int("INDIA_COOLDOWN_SEC", 300)
_MIN_ATR_POINTS: float = config._safe_float("INDIA_MIN_ATR_POINTS", 3.0)
# ATR floor for stock bases, % of price (absolute points are index-scaled —
# 3 NIFTY points is 0.01% of the index but 2.5% of a ₹120 stock).
_MIN_ATR_PCT: float = config._safe_float("INDIA_MIN_ATR_PCT", 0.05)
_MIN_OI: float = config._safe_float("INDIA_MIN_OI", 100_000.0)
# Max signals per (base, direction) per day. The per-setup cooldown still
# spaces repeats; this caps how many distinct same-direction setups can emit
# on one instrument in a session. 1 meant at most 4 signals/day across both
# bases — below the 3+/day target once any single setup misfired; 2 gives
# room for a genuine second same-direction setup without spamming.
_MAX_PER_DIRECTION: int = config._safe_int("INDIA_MAX_SIGNALS_PER_DIRECTION", 2)
# Per-scan flood limiter. With 46 bases a correlated index move can validate a
# dozen near-identical stock breakouts in one scan — subscribers get the best
# few per scan, not a burst. Env-overridable.
_MAX_PER_SCAN: int = config._safe_int("INDIA_MAX_SIGNALS_PER_SCAN", 3)
# Daily total cap. 0 (default) = unlimited — owner decision (Session 15):
# there is no fixed daily signal budget; volume is bounded by quality gates
# (confidence floor, cooldowns, per-direction/base, per-setup and per-group
# flood caps), not by an arbitrary count. The old default of 10 was being
# fully spent in the opening burst, silencing the rest of the day. Set a
# positive value to restore a hard daily ceiling.
_MAX_PER_DAY: int = config._safe_int("INDIA_MAX_SIGNALS_PER_DAY", 0)
# ATR floor for index bases as % of price — the 3.0 absolute-point floor is
# 0.01% of NIFTY and effectively never fired; the % floor keeps a genuinely
# dead session (no tradeable range) from emitting geometry built on noise.
_MIN_ATR_PCT_INDEX: float = config._safe_float("INDIA_MIN_ATR_PCT_INDEX", 0.02)
# Same-direction emissions allowed per correlation group per scan.
_MAX_PER_GROUP_PER_SCAN: int = config._safe_int("INDIA_MAX_PER_GROUP_PER_SCAN", 1)
# Same-direction emissions allowed per *setup class* per scan, across the whole
# universe. The correlation-group gate caps sectors, but one market-wide move
# fires the same evaluator on many bases across *different* groups at once —
# live 2026-07-09 12:42: nine DIVERGENCE_CONTINUATION shorts in one burst, all
# expressions of a single market-wide bounce. Best confidence wins.
_MAX_PER_SETUP_PER_SCAN: int = config._safe_int("INDIA_MAX_PER_SETUP_PER_SCAN", 1)
# Suppress a *stock* signal that fights a non-neutral proxy-index intraday bias
# (dependency pairs). Fighting the anchor index was already the lowest score in
# the index-alignment component; the live data showed score alone doesn't stop
# it — counter-index stock signals still cleared the emit floor and lost.
_INDEX_CONFLICT_GATE: bool = config._safe_bool("INDIA_INDEX_CONFLICT_GATE", True)
# An opposite-direction signal on the same base within this many minutes of an
# emission is suppressed — one of the two calls is wrong, and flip-flopping a
# subscriber inside half an hour costs trust either way.
_CONFLICT_WINDOW_MIN: int = config._safe_int("INDIA_CONFLICT_WINDOW_MIN", 30)


# ── Gate result ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class Suppression:
    """One gate rejection — logged and surfaced via the suppressed endpoint."""

    gate: str
    reason: str
    setup_class: str
    base: str
    direction: str
    ts: datetime


# ── Gate chain ───────────────────────────────────────────────────────

class GateChain:
    """Stateful gate chain — tracks cooldowns and daily emissions."""

    def __init__(self, events: EventCalendar | None = None) -> None:
        self._events = events or EventCalendar()
        self._last_fire: dict[str, datetime] = {}
        self._emitted_today: Counter[tuple[str, str]] = Counter()
        self._emitted_total_today = 0
        self._suppressions: list[Suppression] = []
        # base -> (direction, ts) of its latest emission (direction-conflict gate).
        self._last_base_emission: dict[str, tuple[str, datetime]] = {}
        # (correlation group, direction) -> emissions this scan (group cap).
        self._group_dir_this_scan: Counter[tuple[str, str]] = Counter()
        # (setup class, direction) -> emissions this scan (setup flood cap).
        self._setup_dir_this_scan: Counter[tuple[str, str]] = Counter()

    def begin_scan(self) -> None:
        """Reset the per-scan counters. Called once per scan."""
        self._group_dir_this_scan.clear()
        self._setup_dir_this_scan.clear()

    def rehydrate(self, rows: list[dict], now: datetime) -> None:
        """Rebuild today's emission state from already-persisted signals.

        The gate chain is process-scoped; before this existed, every container
        restart (deploys included) silently re-opened the daily budget and
        wiped cooldowns — live 2026-07-09: four restarts x the 10/day cap = 40
        emissions, in bursts, with duplicates. Each row carries ``age_sec``
        (seconds since emission, computed by SQLite in its own clock frame so
        container-timezone mismatches cannot skew it).
        """
        for row in sorted(
            rows, key=lambda r: float(r.get("age_sec", 0) or 0), reverse=True
        ):
            setup = str(row.get("setup_class", ""))
            base = str(row.get("base", ""))
            direction = str(row.get("direction", ""))
            if not base or not direction:
                continue
            ts = now - timedelta(seconds=max(0.0, float(row.get("age_sec", 0) or 0)))
            if setup:
                key = f"{setup}:{base}"
                prev = self._last_fire.get(key)
                if prev is None or prev < ts:
                    self._last_fire[key] = ts
            self._emitted_today[(base, direction)] += 1
            self._emitted_total_today += 1
            last = self._last_base_emission.get(base)
            if last is None or last[1] < ts:
                self._last_base_emission[base] = (direction, ts)
        if rows:
            logger.info(
                "gate chain rehydrated: {} emissions already today", len(rows)
            )

    def _suppress(
        self,
        gate_name: str,
        reason: str,
        signal: IndiaSignal,
        ctx: IndiaContext,
        now: datetime,
    ) -> str:
        self._suppressions.append(
            Suppression(
                gate=gate_name,
                reason=reason,
                setup_class=signal.setup_class,
                base=ctx.base,
                direction=signal.direction,
                ts=now,
            )
        )
        return gate_name

    def check(
        self,
        signal: IndiaSignal,
        ctx: IndiaContext,
        session_state: SessionState,
        now: datetime,
        confidence: float | None = None,
    ) -> str | None:
        """Run the pre-score gates. Returns ``None`` if all pass, else the
        gate name that suppressed (and appends to ``_suppressions``)."""

        for gate_fn in (
            self._session_gate,
            self._warmup_gate,
            self._stale_data_gate,
            self._spread_gate,
            self._cooldown_gate,
            self._event_risk_gate,
            self._circuit_check_gate,
            self._min_atr_gate,
            self._sl_noise_gate,
            self._min_scalp_gate,
            self._oi_liquidity_gate,
            self._index_conflict_gate,
        ):
            reason = gate_fn(signal, ctx, session_state, now)
            if reason is not None:
                return self._suppress(
                    gate_fn.__name__.lstrip("_"), reason, signal, ctx, now
                )

        if confidence is not None:
            reason = self._confidence_floor_gate(confidence, ctx.is_expiry_day)
            if reason is not None:
                return self._suppress(
                    "confidence_floor_gate", reason, signal, ctx, now
                )

        return None

    def check_confidence_floor(
        self,
        signal: IndiaSignal,
        ctx: IndiaContext,
        confidence: float,
        now: datetime,
    ) -> str | None:
        """Confidence-floor gate only — the pre-score gates already ran, so the
        scanner uses this after scoring instead of re-running the whole chain."""
        reason = self._confidence_floor_gate(confidence, ctx.is_expiry_day)
        if reason is not None:
            return self._suppress(
                "confidence_floor_gate", reason, signal, ctx, now
            )
        return None

    def check_emission(
        self,
        signal: IndiaSignal,
        ctx: IndiaContext,
        now: datetime,
        emitted_this_scan: int,
    ) -> str | None:
        """Emission-stage gates, run highest-confidence-first per scan:
        per-direction daily cap, direction-conflict window, correlation-group
        cap, per-scan cap, universe-wide daily cap."""
        count = self._emitted_today[(ctx.base, signal.direction)]
        if count >= _MAX_PER_DIRECTION:
            return self._suppress(
                "duplicate_direction_gate",
                f"{signal.direction} on {ctx.base} already emitted {count}x"
                f" today (cap {_MAX_PER_DIRECTION})",
                signal,
                ctx,
                now,
            )
        last = self._last_base_emission.get(ctx.base)
        if last is not None:
            last_dir, last_ts = last
            elapsed_min = (now - last_ts).total_seconds() / 60.0
            if last_dir != signal.direction and elapsed_min < _CONFLICT_WINDOW_MIN:
                return self._suppress(
                    "direction_conflict_gate",
                    f"{last_dir} emitted on {ctx.base} {elapsed_min:.0f}m ago —"
                    f" opposite {signal.direction} blocked for"
                    f" {_CONFLICT_WINDOW_MIN}m (no whipsaw)",
                    signal,
                    ctx,
                    now,
                )
        group = dependency.group_for(ctx.base)
        group_count = self._group_dir_this_scan[(group, signal.direction)]
        if group_count >= _MAX_PER_GROUP_PER_SCAN:
            return self._suppress(
                "correlation_group_gate",
                f"{group} group already emitted {group_count} {signal.direction}"
                f" this scan (cap {_MAX_PER_GROUP_PER_SCAN} — correlated setups"
                f" compete, best confidence wins)",
                signal,
                ctx,
                now,
            )
        setup_count = self._setup_dir_this_scan[
            (signal.setup_class, signal.direction)
        ]
        if setup_count >= _MAX_PER_SETUP_PER_SCAN:
            return self._suppress(
                "setup_flood_gate",
                f"{signal.setup_class} already emitted {setup_count}"
                f" {signal.direction} this scan (cap {_MAX_PER_SETUP_PER_SCAN}"
                f" — one market-wide move, one best expression)",
                signal,
                ctx,
                now,
            )
        if emitted_this_scan >= _MAX_PER_SCAN:
            return self._suppress(
                "scan_cap_gate",
                f"scan already emitted {emitted_this_scan}"
                f" (cap {_MAX_PER_SCAN}, lower-confidence candidate dropped)",
                signal,
                ctx,
                now,
            )
        if _MAX_PER_DAY > 0 and self._emitted_total_today >= _MAX_PER_DAY:
            return self._suppress(
                "daily_cap_gate",
                f"{self._emitted_total_today} signals already emitted today"
                f" (cap {_MAX_PER_DAY})",
                signal,
                ctx,
                now,
            )
        return None

    def record_emission(
        self, setup_class: str, base: str, direction: str, now: datetime
    ) -> None:
        key = f"{setup_class}:{base}"
        self._last_fire[key] = now
        self._emitted_today[(base, direction)] += 1
        self._emitted_total_today += 1
        self._last_base_emission[base] = (direction, now)
        self._group_dir_this_scan[(dependency.group_for(base), direction)] += 1
        self._setup_dir_this_scan[(setup_class, direction)] += 1

    def reset_day(self) -> None:
        self._last_fire.clear()
        self._emitted_today.clear()
        self._emitted_total_today = 0
        self._suppressions.clear()
        self._last_base_emission.clear()
        self._group_dir_this_scan.clear()
        self._setup_dir_this_scan.clear()

    @property
    def suppressions(self) -> list[Suppression]:
        return list(self._suppressions)

    # ── Individual gates ─────────────────────────────────────────────

    @staticmethod
    def _session_gate(
        signal: IndiaSignal,
        ctx: IndiaContext,
        session_state: SessionState,
        now: datetime,
    ) -> str | None:
        if session_state != SessionState.OPEN:
            return f"session {session_state.value}, not OPEN"
        return None

    @staticmethod
    def _warmup_gate(
        signal: IndiaSignal,
        ctx: IndiaContext,
        session_state: SessionState,
        now: datetime,
    ) -> str | None:
        """No emissions before WARMUP_END (default 09:30 IST).

        The first minutes are auction noise on half-formed intraday state:
        live 2026-07-09 the engine spent its entire daily budget inside
        09:15-09:16 (10 signals, including every A+ of the day) and every
        resolved one hit SL.
        """
        if config.INDIA_DEV_MODE:
            return None  # dev mode exercises the pipeline off-hours
        t = ctx.scan_time_ist
        if t is not None and t.replace(tzinfo=None) < config.WARMUP_END:
            return (
                f"{t.strftime('%H:%M')} inside session warm-up"
                f" (no signals before {config.WARMUP_END.strftime('%H:%M')} IST)"
            )
        return None

    @staticmethod
    def _stale_data_gate(
        signal: IndiaSignal,
        ctx: IndiaContext,
        session_state: SessionState,
        now: datetime,
    ) -> str | None:
        """No emission from a symbol whose live tick flow has stopped.

        Live 2026-07-10: the WebSocket died silently, the scanner kept
        scanning the static morning seed and emitted duplicate signals with
        identical hour-old entries nobody could fill. A candidate is only
        tradeable if its data is moving: no live tick ever (seed-only
        buffer) or a newest tick older than ``MAX_TICK_AGE_SEC`` suppresses
        with telemetry — the first stop when "signals look frozen".
        """
        if config.INDIA_DEV_MODE:
            return None  # dev mode exercises the pipeline without a feed
        age = ctx.last_tick_age_sec
        if age is None:
            return (
                f"no live tick ever received for {ctx.base} — scanning"
                " seed-only data (feed dead or never subscribed)"
            )
        if age > config.MAX_TICK_AGE_SEC:
            return (
                f"newest {ctx.base} tick is {age:.0f}s old"
                f" (limit {config.MAX_TICK_AGE_SEC}s) — data frozen,"
                " entry would be unfillable"
            )
        return None

    @staticmethod
    def _spread_gate(
        signal: IndiaSignal,
        ctx: IndiaContext,
        session_state: SessionState,
        now: datetime,
    ) -> str | None:
        return None

    def _cooldown_gate(
        self,
        signal: IndiaSignal,
        ctx: IndiaContext,
        session_state: SessionState,
        now: datetime,
    ) -> str | None:
        key = f"{signal.setup_class}:{ctx.base}"
        last = self._last_fire.get(key)
        if last is not None:
            elapsed = (now - last).total_seconds()
            if elapsed < _COOLDOWN_SEC:
                remaining = int(_COOLDOWN_SEC - elapsed)
                return (
                    f"{signal.setup_class} on {ctx.base} fired {int(elapsed)}s ago,"
                    f" {remaining}s cooldown left"
                )
        return None

    def _event_risk_gate(
        self,
        signal: IndiaSignal,
        ctx: IndiaContext,
        session_state: SessionState,
        now: datetime,
    ) -> str | None:
        if ctx.india_vix > _VIX_EVENT_THRESHOLD:
            return f"VIX {ctx.india_vix:.1f} > {_VIX_EVENT_THRESHOLD} event threshold"
        event = self._events.event_on(now.date())
        if event is not None:
            return f"macro event day: {event} (IB13 — no signals)"
        return None

    @staticmethod
    def _circuit_check_gate(
        signal: IndiaSignal,
        ctx: IndiaContext,
        session_state: SessionState,
        now: datetime,
    ) -> str | None:
        if ctx.day_open > 0 and ctx.candles_5m:
            last_close = ctx.candles_5m[-1].close
            move_pct = abs(last_close - ctx.day_open) / ctx.day_open * 100
            if move_pct > _CIRCUIT_MOVE_PCT:
                return f"intraday move {move_pct:.1f}% > {_CIRCUIT_MOVE_PCT}% circuit threshold"
        return None

    @staticmethod
    def _min_atr_gate(
        signal: IndiaSignal,
        ctx: IndiaContext,
        session_state: SessionState,
        now: datetime,
    ) -> str | None:
        # Index bases: the larger of the absolute-point floor and a % of price
        # (3.0 points alone is 0.01% of NIFTY — it never fired). Stock bases:
        # % of price (a fixed point floor either suppresses every cheap stock
        # or is meaningless on an expensive one).
        last = ctx.candles_5m[-1].close if ctx.candles_5m else 0.0
        if ctx.base in config.INSTRUMENTS:
            floor = max(_MIN_ATR_POINTS, last * _MIN_ATR_PCT_INDEX / 100.0)
            if ctx.atr14_5m < floor:
                return (
                    f"ATR {ctx.atr14_5m:.1f} < {floor:.1f} minimum"
                    f" (max of {_MIN_ATR_POINTS} pts, {_MIN_ATR_PCT_INDEX}%)"
                )
            return None
        floor = last * _MIN_ATR_PCT / 100.0
        if last > 0 and ctx.atr14_5m < floor:
            return (
                f"ATR {ctx.atr14_5m:.2f} < {floor:.2f}"
                f" ({_MIN_ATR_PCT}% of price) minimum"
            )
        return None

    @staticmethod
    def _sl_noise_gate(
        signal: IndiaSignal,
        ctx: IndiaContext,
        session_state: SessionState,
        now: datetime,
    ) -> str | None:
        """A stop narrower than MIN_SL_ATR_MULT x ATR sits inside one bar's
        expected range — the trade is a coin flip on the next wick no matter
        how clean the setup logic was. Live 2026-07-08/09 the SL_HIT cluster
        concentrated in exactly these sub-bar stops."""
        if ctx.atr14_5m <= 0:
            return None
        sl_dist = abs(signal.entry - signal.sl)
        floor = ctx.atr14_5m * config.MIN_SL_ATR_MULT
        if sl_dist < floor:
            return (
                f"SL distance {sl_dist:.2f} < {floor:.2f}"
                f" ({config.MIN_SL_ATR_MULT}x ATR) — stop inside bar noise"
            )
        return None

    @staticmethod
    def _index_conflict_gate(
        signal: IndiaSignal,
        ctx: IndiaContext,
        session_state: SessionState,
        now: datetime,
    ) -> str | None:
        """Dependency pairs, enforced: a stock signal fighting a non-neutral
        proxy-index intraday bias is suppressed (indices themselves are exempt
        — they legitimately diverge from each other). Scoring already zeroed
        the alignment component; the live data showed counter-index stock
        signals still cleared the floor and lost."""
        if not _INDEX_CONFLICT_GATE:
            return None
        if ctx.base in config.INDEX_BASES:
            return None
        if (
            ctx.index_bias != dependency.NEUTRAL
            and signal.direction != ctx.index_bias
        ):
            return (
                f"{signal.direction} fights the {ctx.index_bias} intraday bias"
                f" of its proxy index (dependency-pair gate)"
            )
        return None

    @staticmethod
    def _min_scalp_gate(
        signal: IndiaSignal,
        ctx: IndiaContext,
        session_state: SessionState,
        now: datetime,
    ) -> str | None:
        """IB11 — TP1 must clear the STT-viable minimum move (15 NIFTY /
        40 BANKNIFTY points; % of price for stocks). Below it the trade
        cannot pay for its own round-trip costs even when it wins."""
        tp1_points = abs(signal.tp1 - signal.entry)
        floor = config.min_scalp_points_for(ctx.base, signal.entry)
        if tp1_points < floor:
            return (
                f"TP1 distance {tp1_points:.1f} pts < IB11 minimum"
                f" {floor:.1f} pts (STT-viable floor)"
            )
        return None

    @staticmethod
    def _oi_liquidity_gate(
        signal: IndiaSignal,
        ctx: IndiaContext,
        session_state: SessionState,
        now: datetime,
    ) -> str | None:
        if ctx.current_oi > 0 and ctx.current_oi < _MIN_OI:
            return f"OI {ctx.current_oi:,.0f} < {_MIN_OI:,.0f} minimum"
        return None

    @staticmethod
    def _confidence_floor_gate(
        confidence: float, is_expiry_day: bool = False
    ) -> str | None:
        floor = config.CONFIDENCE_EMIT_FLOOR
        note = ""
        if is_expiry_day:
            floor += config.EXPIRY_CONFIDENCE_BUMP
            note = f" (expiry-day +{config.EXPIRY_CONFIDENCE_BUMP:.0f}, IB16)"
        if confidence < floor:
            return f"confidence {confidence:.0f} < {floor:.0f} floor{note}"
        return None


# ── Scanner ──────────────────────────────────────────────────────────

def _build_evaluators() -> list[Evaluator]:
    """Instantiate all 14 evaluators."""
    return [
        LiquiditySweepReversal(),
        OpeningRangeBreakout(),
        TrendPullbackEma(),
        VolumeSurgeBreakout(),
        BreakdownShort(),
        SrFlipRetest(),
        IndiaVixExtreme(),
        PcrExtreme(),
        FailedAuctionReclaim(),
        DivergenceContinuation(),
        QuietCompressionBreak(),
        MaCrossTrendShift(),
        OiSpikeReversal(),
        ExpiryGammaSqueeze(),
    ]


class IndiaScanner:
    """Orchestrates evaluators + gates + scoring for one scan cycle."""

    def __init__(
        self,
        context_builder: IndiaContextBuilder,
        session_mgr: SessionManager,
        expiry_mgr: ExpiryManager,
        evaluators: list[Evaluator] | None = None,
    ) -> None:
        self._ctx_builder = context_builder
        self._session = session_mgr
        self._expiry = expiry_mgr
        self._evaluators = evaluators or _build_evaluators()
        self._scorer = IndiaSignalScoringEngine()
        self._gates = GateChain()
        self._scan_count = 0

    @property
    def gates(self) -> GateChain:
        return self._gates

    def rehydrate(self, rows: list[dict], now: datetime) -> None:
        """Restore today's emission state after a restart (see GateChain)."""
        self._gates.rehydrate(rows, now)

    def scan(
        self,
        symbols: dict[str, str],
        now: datetime,
    ) -> list[IndiaSignal]:
        """Run one scan cycle across all symbols.

        ``symbols`` maps base name → Fyers symbol
        (e.g. ``{"NIFTY": "NSE:NIFTY26JULFUT", ...}``).

        Returns emitted (scored, tiered) signals.
        """
        self._scan_count += 1
        session_state = self._session.current_state(now)

        if session_state != SessionState.OPEN and not config.INDIA_DEV_MODE:
            return []

        self._gates.begin_scan()

        # Pass 1 — build every context, then anchor each base to its proxy
        # index's intraday bias (dependency pairs). Index contexts must exist
        # before any stock is evaluated, so this cannot fold into the eval loop.
        contexts: dict[str, IndiaContext] = {}
        for base, symbol in symbols.items():
            if base not in config.ALLOWED_BASES:
                continue
            ctx = self._ctx_builder.build(symbol, base, now)
            if not ctx.candles_5m:
                logger.debug("skip {} — no 5m candles", base)
                continue
            contexts[base] = ctx

        index_biases = {
            base: dependency.market_bias(ctx)
            for base, ctx in contexts.items()
            if base in config.INDEX_BASES
        }
        for base, ctx in contexts.items():
            for proxy in dependency.proxy_candidates(base):
                bias = index_biases.get(proxy)
                if bias is not None:
                    ctx.index_bias = bias
                    break

        scored: list[tuple[IndiaSignal, IndiaContext]] = []

        # Pass 2 — evaluate, gate, and score every base.
        for base, ctx in contexts.items():
            is_index = base in config.INDEX_BASES

            for ev in self._evaluators:
                if not ev.enabled:
                    continue
                # Index-only setups (market-wide PCR / index max-pain) have no
                # per-stock equivalent — skip them for stock bases.
                if ev.index_only and not is_index:
                    continue

                try:
                    candidate = ev.evaluate(ctx)
                except Exception:
                    logger.opt(exception=True).warning(
                        "evaluator {} raised on {}", ev.setup_class, base
                    )
                    continue

                if candidate is None:
                    continue

                pre_gate = self._gates.check(
                    candidate, ctx, session_state, now
                )
                if pre_gate is not None:
                    logger.debug(
                        "suppressed {} on {} by {}",
                        candidate.setup_class,
                        base,
                        pre_gate,
                    )
                    continue

                confidence = self._scorer.score(candidate, ctx)
                candidate.confidence = confidence
                candidate.tier = tier_for(confidence)

                floor_gate = self._gates.check_confidence_floor(
                    candidate, ctx, confidence, now
                )
                if floor_gate is not None:
                    logger.debug(
                        "suppressed {} on {} by {} (confidence {:.0f})",
                        candidate.setup_class,
                        base,
                        floor_gate,
                        confidence,
                    )
                    continue

                scored.append((candidate, ctx))

        # Emission stage — the whole scan's survivors compete on confidence,
        # so when a correlated move validates many setups at once the caps
        # keep the best few instead of whichever base iterated first.
        scored.sort(key=lambda pair: pair[0].confidence, reverse=True)

        emitted: list[IndiaSignal] = []
        for candidate, ctx in scored:
            emit_gate = self._gates.check_emission(
                candidate, ctx, now, emitted_this_scan=len(emitted)
            )
            if emit_gate is not None:
                logger.debug(
                    "suppressed {} on {} by {}",
                    candidate.setup_class,
                    ctx.base,
                    emit_gate,
                )
                continue

            candidate.regime_60m = ctx.regime_60m
            candidate.regime_daily = ctx.regime_daily
            candidate.atr_at_entry = ctx.atr14_5m
            candidate.vix_at_entry = ctx.india_vix
            candidate.expiry_date = self._expiry.get_contract_expiry_date(now)
            candidate.days_to_expiry = self._expiry.days_to_expiry(now)

            self._gates.record_emission(
                candidate.setup_class, ctx.base, candidate.direction, now
            )
            emitted.append(candidate)

            logger.info(
                "SIGNAL {} {} {} conf={:.0f} tier={}",
                candidate.setup_class,
                ctx.base,
                candidate.direction,
                candidate.confidence,
                candidate.tier,
            )

        return emitted

    def reset_day(self) -> None:
        self._gates.reset_day()
        self._scan_count = 0
