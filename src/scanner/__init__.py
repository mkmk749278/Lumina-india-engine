"""India scanner — 30s scan loop + 9-gate chain + scoring.

Runs every ``SCAN_INTERVAL_SEC`` during market hours. For each allowed
base (NIFTY, BANKNIFTY), builds an ``IndiaContext``, runs all enabled
evaluators, gates candidates through the 9-gate chain, scores survivors,
and returns emitted signals for the signal router.

Gate chain (spec §9):
  1. session_gate — market hours?
  2. spread_gate — bid-ask too wide? (Phase 2 — always pass in Phase 1)
  3. cooldown_gate — evaluator fired recently?
  4. event_risk_gate — VIX > 25 or macro event?
  5. circuit_check_gate — extreme intraday move?
  6. min_atr_gate — ATR above tradeable threshold?
  7. oi_liquidity_gate — enough open interest?
  8. duplicate_direction_gate — same-direction signal already emitted?
  9. confidence_floor_gate — score >= emit floor?

Every gate rejection is logged with gate name + reason (suppression
telemetry — CLAUDE.md). Surface via ``/api/india/suppressed`` and ops.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime

import config
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
_MIN_OI: float = config._safe_float("INDIA_MIN_OI", 100_000.0)
# Max signals per (base, direction) per day. The per-setup cooldown still
# spaces repeats; this caps how many distinct same-direction setups can emit
# on one instrument in a session. 1 meant at most 4 signals/day across both
# bases — below the 3+/day target once any single setup misfired; 2 gives
# room for a genuine second same-direction setup without spamming.
_MAX_PER_DIRECTION: int = config._safe_int("INDIA_MAX_SIGNALS_PER_DIRECTION", 2)


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

    def __init__(self) -> None:
        self._last_fire: dict[str, datetime] = {}
        self._emitted_today: Counter[tuple[str, str]] = Counter()
        self._suppressions: list[Suppression] = []

    def check(
        self,
        signal: IndiaSignal,
        ctx: IndiaContext,
        session_state: SessionState,
        now: datetime,
        confidence: float | None = None,
    ) -> str | None:
        """Run the 9-gate chain. Returns ``None`` if all pass, else the
        gate name that suppressed (and appends to ``_suppressions``)."""

        for gate_fn in (
            self._session_gate,
            self._spread_gate,
            self._cooldown_gate,
            self._event_risk_gate,
            self._circuit_check_gate,
            self._min_atr_gate,
            self._oi_liquidity_gate,
            self._duplicate_direction_gate,
        ):
            reason = gate_fn(signal, ctx, session_state, now)
            if reason is not None:
                gate_name = gate_fn.__name__.lstrip("_")
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

        if confidence is not None:
            reason = self._confidence_floor_gate(confidence)
            if reason is not None:
                self._suppressions.append(
                    Suppression(
                        gate="confidence_floor_gate",
                        reason=reason,
                        setup_class=signal.setup_class,
                        base=ctx.base,
                        direction=signal.direction,
                        ts=now,
                    )
                )
                return "confidence_floor_gate"

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
        reason = self._confidence_floor_gate(confidence)
        if reason is not None:
            self._suppressions.append(
                Suppression(
                    gate="confidence_floor_gate",
                    reason=reason,
                    setup_class=signal.setup_class,
                    base=ctx.base,
                    direction=signal.direction,
                    ts=now,
                )
            )
            return "confidence_floor_gate"
        return None

    def record_emission(
        self, setup_class: str, base: str, direction: str, now: datetime
    ) -> None:
        key = f"{setup_class}:{base}"
        self._last_fire[key] = now
        self._emitted_today[(base, direction)] += 1

    def reset_day(self) -> None:
        self._last_fire.clear()
        self._emitted_today.clear()
        self._suppressions.clear()

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

    @staticmethod
    def _event_risk_gate(
        signal: IndiaSignal,
        ctx: IndiaContext,
        session_state: SessionState,
        now: datetime,
    ) -> str | None:
        if ctx.india_vix > _VIX_EVENT_THRESHOLD:
            return f"VIX {ctx.india_vix:.1f} > {_VIX_EVENT_THRESHOLD} event threshold"
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
        if ctx.atr14_5m < _MIN_ATR_POINTS:
            return f"ATR {ctx.atr14_5m:.1f} < {_MIN_ATR_POINTS} minimum"
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

    def _duplicate_direction_gate(
        self,
        signal: IndiaSignal,
        ctx: IndiaContext,
        session_state: SessionState,
        now: datetime,
    ) -> str | None:
        count = self._emitted_today[(ctx.base, signal.direction)]
        if count >= _MAX_PER_DIRECTION:
            return (
                f"{signal.direction} on {ctx.base} already emitted {count}x today"
                f" (cap {_MAX_PER_DIRECTION})"
            )
        return None

    @staticmethod
    def _confidence_floor_gate(confidence: float) -> str | None:
        if confidence < config.CONFIDENCE_EMIT_FLOOR:
            return f"confidence {confidence:.0f} < {config.CONFIDENCE_EMIT_FLOOR:.0f} floor"
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

        emitted: list[IndiaSignal] = []

        for base, symbol in symbols.items():
            if base not in config.ALLOWED_BASES:
                continue

            ctx = self._ctx_builder.build(symbol, base, now)

            if not ctx.candles_5m:
                logger.debug("skip {} — no 5m candles", base)
                continue

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

                candidate.regime_60m = ctx.regime_60m
                candidate.regime_daily = ctx.regime_daily
                candidate.atr_at_entry = ctx.atr14_5m
                candidate.vix_at_entry = ctx.india_vix
                candidate.expiry_date = self._expiry.get_contract_expiry_date(now)
                candidate.days_to_expiry = self._expiry.days_to_expiry(now)

                self._gates.record_emission(
                    candidate.setup_class, base, candidate.direction, now
                )
                emitted.append(candidate)

                logger.info(
                    "SIGNAL {} {} {} conf={:.0f} tier={}",
                    candidate.setup_class,
                    base,
                    candidate.direction,
                    confidence,
                    candidate.tier,
                )

        return emitted

    def reset_day(self) -> None:
        self._gates.reset_day()
        self._scan_count = 0
