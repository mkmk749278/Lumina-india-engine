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
 13. chop_gate — is there any directional regime to trade? (both the
     60m and daily regime RANGING/QUIET is chop — live 2026-07-10 that
     bucket went 0/8 resolved)
 14. tp_feasibility_gate — is TP1 reachable in the session time left
     at the current ATR pace? (far targets emitted late expire at the
     close instead of resolving)

Post-score:
 15. confidence_floor_gate — score >= emit floor (+5 on expiry day, IB16)?

Emission stage (highest confidence first across the whole scan):
 16. duplicate_direction_gate — same-direction cap per base per day
 17. direction_conflict_gate — no opposite-direction signal on a base
     minutes after one was emitted (whipsawing subscribers is worse
     than missing a genuine V-reversal)
 18. correlation_group_gate — same-direction cap per sector group per
     scan: one index move validates several near-identical correlated
     setups at once; subscribers get the best one, not three copies
 19. setup_flood_gate — same-direction cap per setup class per scan:
     one market-wide move fires the same evaluator across many groups
 20. scan_cap_gate — per-scan flood limiter (best few per scan)
 21. daily_cap_gate — optional hard daily ceiling; OFF by default
     (INDIA_MAX_SIGNALS_PER_DAY=0 — owner decision: no fixed daily
     signal budget, volume is bounded by quality gates, not a count)

Restart safety: the chain's day-cumulative state (daily caps, cooldowns,
per-base last emission) is rehydrated from ``india_signals`` at boot —
a mid-session deploy no longer re-opens the daily budget.

Every gate rejection is logged with gate name + reason (suppression
telemetry — CLAUDE.md). Surface via ``/api/india/suppressed`` and ops.
"""

from __future__ import annotations

import json
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
from src.market_context import (
    MarketContext,
    MarketDirection,
    classify_session_phase,
)
from src.regime import Regime
from src.session.event_calendar import EventCalendar
from src.session.expiry_manager import ExpiryManager
from src.session.session_manager import SessionManager, SessionState
from src.signal_quality import IndiaSignalScoringEngine, tier_for
from src.signals.model import (
    SETUP_FAMILY,
    Direction,
    IndiaContext,
    IndiaSignal,
    SetupFamily,
    is_trend_family,
)
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
# Same setup+base re-emission spacing. 300s allowed near-duplicate pairs 5-9
# minutes apart (live 2026-07-10: 12 of 88 emissions were repeats of the same
# base+setup+direction inside 15 minutes, same entry to the decimal) — a
# subscriber who saw the first signal gains nothing from its echo. 900s means
# a setup must re-qualify on genuinely new structure, not the same bar cluster.
_COOLDOWN_SEC: int = config._safe_int("INDIA_COOLDOWN_SEC", 900)
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
# Double-chop suppression. Regime previously only fed scoring — a candidate
# with no directional regime on EITHER timeframe scored the neutral tiers and
# still cleared the emit floor. Live 2026-07-10 (13:49-15:19, first post-#52
# window): every such candidate lost — 0/8 resolved, -1.01% gross, across
# multiple setup classes. The exempt list is the escape hatch if a specific
# setup later proves range-profitable in the 30-day ledger (CSV of
# SetupClass names, e.g. "LIQUIDITY_SWEEP_REVERSAL,PCR_EXTREME").
_CHOP_GATE_ENABLED: bool = config._safe_bool("INDIA_CHOP_GATE_ENABLED", True)
_CHOP_EXEMPT_SETUPS: frozenset[str] = frozenset(
    s.strip().upper()
    for s in config._safe_str("INDIA_CHOP_GATE_EXEMPT_SETUPS", "").split(",")
    if s.strip()
)
_CHOP_REGIMES = (Regime.RANGING, Regime.QUIET)
# Regime/setup-compatibility gate. `_chop_gate` only fires when BOTH timeframes
# are non-directional; a trend-continuation setup whose 60m looks "trending"
# (noise inside a ranging day) still sails through when the *daily* regime is
# RANGING/QUIET. Live 2026-07-14: TREND-family setups in a ranging daily regime
# went 3/23 (13%, -3.76% gross) — essentially the whole day's loss — while
# reversion/breakout setups in the same tape won 50%. A trend setup needs the
# higher timeframe to actually trend; a ranging daily is the absence of what it
# trades. Exempt list is the escape hatch (CSV of SetupClass names).
_REGIME_SETUP_GATE_ENABLED: bool = config._safe_bool(
    "INDIA_REGIME_SETUP_GATE_ENABLED", True
)
_REGIME_SETUP_EXEMPT_SETUPS: frozenset[str] = frozenset(
    s.strip().upper()
    for s in config._safe_str("INDIA_REGIME_SETUP_GATE_EXEMPT_SETUPS", "").split(",")
    if s.strip()
)
_REGIME_SETUP_REGIMES = (Regime.RANGING, Regime.QUIET)
# Market-direction gate. The scorer's index-alignment component (5 pts) never
# stopped counter-trend signals from clearing the floor: live 2026-07-13 the
# tape was decisively LONG-biased all day and SHORT signals went 6/45 (13%,
# -5.6%) while LONGs went 28/50 (56%, +11.6%). This gate suppresses a signal
# that fights a *decisive* whole-market direction (MarketContext, needs two
# aligned index votes and zero opposing — NEUTRAL never suppresses, so a
# genuinely two-sided tape is untouched). Distinct from _index_conflict_gate,
# which is a *stock* vs its *proxy index*; this is *any* base (incl. the
# indices themselves) vs the *whole market*. Exempt list is the escape hatch
# for setups that are deliberately contrarian at an extreme (e.g. PCR_EXTREME,
# INDIA_VIX_EXTREME) — CSV of SetupClass names.
_DIRECTION_BIAS_GATE_ENABLED: bool = config._safe_bool(
    "INDIA_DIRECTION_BIAS_GATE_ENABLED", True
)
_DIRECTION_GATE_EXEMPT_SETUPS: frozenset[str] = frozenset(
    s.strip().upper()
    for s in config._safe_str("INDIA_DIRECTION_GATE_EXEMPT_SETUPS", "").split(",")
    if s.strip()
)
# TP1 must be reachable in the session time remaining. Live 2026-07-10: every
# signal with rr > 2.5 lost (0/7) and tp1_pct > 0.25% ran ~11% win — targets
# mapped to far swing/book levels late in the day expire at 15:30 instead of
# resolving. Budget = ATR14(5m) x 5m-bars-remaining x efficiency: at midday
# (~50 bars) it is ~15x ATR and never binds; at the 15:00 last-signal cutoff
# (6 bars) it is ~1.8x ATR — exactly where the failures concentrated.
# Efficiency 0.30 ≈ how much of its per-bar ATR a scalp actually converts
# into directional progress.
_TP_FEASIBILITY_ENABLED: bool = config._safe_bool(
    "INDIA_TP_FEASIBILITY_ENABLED", True
)
_TP_FEASIBILITY_EFFICIENCY: float = config._safe_float(
    "INDIA_TP_FEASIBILITY_EFFICIENCY", 0.30
)


def _parse_phase_blocklist(raw: str) -> frozenset[tuple[str, str]]:
    """CSV of FAMILY:PHASE pairs -> {(family, phase)}. Malformed entries are
    skipped (an unparseable blocklist must fail open, not suppress)."""
    pairs: set[tuple[str, str]] = set()
    for entry in raw.split(","):
        family, sep, phase = entry.strip().partition(":")
        if sep and family.strip() and phase.strip():
            pairs.add((family.strip().upper(), phase.strip().upper()))
    return frozenset(pairs)


_PHASE_BLOCKLIST: frozenset[tuple[str, str]] = _parse_phase_blocklist(
    config.PHASE_BLOCKLIST
)


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
        # (base, direction) -> entry of the latest emission (duplicate
        # entry-move policy: a re-fire must print genuinely new structure).
        self._last_entry: dict[tuple[str, str], float] = {}
        # "SETUP/DIRECTION" cohorts the allocator marks SUPPRESS, loaded at
        # session open. Acted on only when INDIA_ALLOCATOR_ARMED; otherwise
        # every would-suppress is dark-logged so the owner can watch the
        # verdicts track outcomes before arming (owner sign-off).
        self._allocator_suppress: frozenset[str] = frozenset()

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
                entry = float(row.get("entry", 0) or 0)
                if entry > 0:
                    self._last_entry[(base, direction)] = entry
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
            self._cooldown_gate,
            self._event_risk_gate,
            self._circuit_check_gate,
            self._min_atr_gate,
            self._sl_noise_gate,
            self._min_scalp_gate,
            self._oi_liquidity_gate,
            self._index_conflict_gate,
            self._direction_bias_gate,
            # Last on purpose: their suppression counts then measure only
            # candidates every existing viability gate already passed — the
            # exact "would have emitted" population needed to judge these
            # gates against the next live window — and the older gates'
            # week-over-week telemetry attribution stays comparable.
            self._chop_gate,
            self._regime_setup_gate,
            self._tp_feasibility_gate,
            self._phase_affinity_gate,
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
        # Duplicate entry-move policy (default OFF): a same-(base, direction)
        # re-fire whose entry sits within k×ATR of the previous emission's
        # entry is the same idea echoing, not new structure.
        if config.DUP_MIN_ENTRY_MOVE_ATR > 0 and ctx.atr14_5m > 0:
            prev_entry = self._last_entry.get((ctx.base, signal.direction))
            if prev_entry is not None:
                moved = abs(signal.entry - prev_entry)
                floor = ctx.atr14_5m * config.DUP_MIN_ENTRY_MOVE_ATR
                if moved < floor:
                    return self._suppress(
                        "duplicate_entry_gate",
                        f"entry {signal.entry:.1f} only {moved:.1f} pts from"
                        f" the previous {signal.direction} emission on"
                        f" {ctx.base} ({prev_entry:.1f}) — needs ≥{floor:.1f}"
                        f" ({config.DUP_MIN_ENTRY_MOVE_ATR}x ATR) of new"
                        " structure",
                        signal,
                        ctx,
                        now,
                    )
        # Allocator SUPPRESS (owner sign-off to arm): cohorts with n ≥
        # min-sample and expectancy ≤ the suppress threshold, re-derived
        # from the ledger at each session open (self-reversing). HOLD and
        # INSUFFICIENT_DATA never reach this set.
        cohort = f"{signal.setup_class}/{signal.direction}"
        if cohort in self._allocator_suppress:
            if config.ALLOCATOR_ARMED:
                return self._suppress(
                    "allocator_suppress_gate",
                    f"{cohort} is a measured-negative cohort"
                    " (allocator SUPPRESS, re-derived daily)",
                    signal,
                    ctx,
                    now,
                )
            logger.info(
                "allocator would-suppress (dark, not armed): {} on {}"
                " conf={:.0f}",
                cohort,
                ctx.base,
                signal.confidence,
            )
        return None

    def emitted_count(self, base: str, direction: str) -> int:
        """How many (base, direction) emissions have happened today —
        the duplicate ordinal the scanner stamps on each signal."""
        return self._emitted_today[(base, direction)]

    def record_emission(
        self,
        setup_class: str,
        base: str,
        direction: str,
        now: datetime,
        entry: float = 0.0,
    ) -> None:
        key = f"{setup_class}:{base}"
        self._last_fire[key] = now
        self._emitted_today[(base, direction)] += 1
        self._emitted_total_today += 1
        self._last_base_emission[base] = (direction, now)
        self._group_dir_this_scan[(dependency.group_for(base), direction)] += 1
        self._setup_dir_this_scan[(setup_class, direction)] += 1
        if entry > 0:
            self._last_entry[(base, direction)] = entry

    def set_allocator_suppress(self, cohorts: frozenset[str]) -> None:
        """Install the session's allocator SUPPRESS cohort set (loaded once
        at session open from the ledger — never per scan)."""
        self._allocator_suppress = cohorts
        if cohorts:
            logger.info(
                "allocator suppress set loaded ({}): {}",
                "ARMED" if config.ALLOCATOR_ARMED else "dark",
                sorted(cohorts),
            )

    def reset_day(self) -> None:
        self._last_fire.clear()
        self._emitted_today.clear()
        self._emitted_total_today = 0
        self._suppressions.clear()
        self._last_base_emission.clear()
        self._group_dir_this_scan.clear()
        self._setup_dir_this_scan.clear()
        self._last_entry.clear()

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

    # NOTE: the Phase-1 spread gate stub was removed (Session 21). The Fyers
    # lite-mode tick carries no bid/ask, so an honest spread gate has no
    # input — and CLAUDE.md bans always-pass stubs. Re-add when Phase 2's
    # depth-mode data exists.

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
    def _direction_bias_gate(
        signal: IndiaSignal,
        ctx: IndiaContext,
        session_state: SessionState,
        now: datetime,
    ) -> str | None:
        """Suppress a signal fighting a *decisive* whole-market direction
        (MarketContext.market_direction, stamped on ctx). NEUTRAL never
        suppresses — the label needs two aligned index votes and zero opposing
        — so only a one-sided tape haircuts counter-trend. Live 2026-07-13:
        SHORT 6/45 (13%) vs LONG 28/50 (56%) in a LONG-biased tape."""
        if not _DIRECTION_BIAS_GATE_ENABLED:
            return None
        if signal.setup_class in _DIRECTION_GATE_EXEMPT_SETUPS:
            return None
        md = ctx.market_direction
        opposing = (
            (md == MarketDirection.LONG_BIASED and signal.direction == Direction.SHORT)
            or (
                md == MarketDirection.SHORT_BIASED
                and signal.direction == Direction.LONG
            )
        )
        if opposing:
            return (
                f"{signal.direction} fights the {md} whole-market direction"
                f" (market-direction gate)"
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
    def _chop_gate(
        signal: IndiaSignal,
        ctx: IndiaContext,
        session_state: SessionState,
        now: datetime,
    ) -> str | None:
        """No directional regime on either timeframe = chop. Live 2026-07-10
        (13:49-15:19): candidates with BOTH the 60m and daily regime
        RANGING/QUIET went 0/8 resolved, -1.01% gross, across setup classes.
        Regime previously only fed scoring — the neutral HTF tiers still
        cleared the emit floor. Deliberately active in INDIA_DEV_MODE (regime
        forms from seeded history, same posture as min_atr_gate) — dev smoke
        runs on ranging seeds will honestly show chop suppressions."""
        if not _CHOP_GATE_ENABLED:
            return None
        if signal.setup_class in _CHOP_EXEMPT_SETUPS:
            return None
        if ctx.regime_60m in _CHOP_REGIMES and ctx.regime_daily in _CHOP_REGIMES:
            return (
                f"chop: 60m {ctx.regime_60m.value} + daily"
                f" {ctx.regime_daily.value} — no directional regime to trade"
            )
        return None

    @staticmethod
    def _regime_setup_gate(
        signal: IndiaSignal,
        ctx: IndiaContext,
        session_state: SessionState,
        now: datetime,
    ) -> str | None:
        """Trend-continuation setups need a trending higher timeframe. When the
        *daily* regime is RANGING/QUIET a trend setup is fighting the tape even
        if its 60m looks trending (chop inside a range). Live 2026-07-14: the
        TREND family in a ranging daily went 3/23 (13%, -3.76% gross), the day's
        loss; reversion/breakout in the same tape won 50%. Only the TREND family
        is gated — reversion/breakout/neutral setups are untouched. Exempt list
        is the escape hatch if a trend setup later proves range-viable in the
        30-day ledger."""
        if not _REGIME_SETUP_GATE_ENABLED:
            return None
        if not is_trend_family(signal.setup_class):
            return None
        if signal.setup_class in _REGIME_SETUP_EXEMPT_SETUPS:
            return None
        if ctx.regime_daily in _REGIME_SETUP_REGIMES:
            return (
                f"regime/setup: trend-continuation {signal.setup_class} in a"
                f" {ctx.regime_daily.value} daily regime — no trend to continue"
            )
        return None

    @staticmethod
    def _tp_feasibility_gate(
        signal: IndiaSignal,
        ctx: IndiaContext,
        session_state: SessionState,
        now: datetime,
    ) -> str | None:
        """TP1 must be reachable before the 15:30 close at the current ATR
        pace. Live 2026-07-10: rr > 2.5 went 0/7 and tp1_pct > 0.25% ran ~11%
        win — far level-mapped targets emitted late expire instead of
        resolving. Deadline is MARKET_CLOSE, not FORCE_CLOSE_TIME: outcomes
        resolve through the CLOSING window and EXPIRED is only scored at
        15:30 (Phase 1 measurement; 15:25 is a Phase-2 execution concept)."""
        if not _TP_FEASIBILITY_ENABLED:
            return None
        if config.INDIA_DEV_MODE:
            return None  # off-hours dev scans have 0 bars remaining
        t = ctx.scan_time_ist
        if t is None or ctx.atr14_5m <= 0:
            return None
        close = config.MARKET_CLOSE
        minutes_left = (
            (close.hour * 60 + close.minute)
            - (t.hour * 60 + t.minute + t.second / 60.0)
        )
        bars_remaining = max(0.0, minutes_left / 5.0)
        budget = ctx.atr14_5m * bars_remaining * _TP_FEASIBILITY_EFFICIENCY
        tp1_dist = abs(signal.tp1 - signal.entry)
        if tp1_dist > budget:
            return (
                f"TP1 {tp1_dist:.1f} pts unreachable before"
                f" {close.strftime('%H:%M')} — budget {budget:.1f} pts"
                f" ({bars_remaining:.0f} 5m bars x ATR {ctx.atr14_5m:.1f}"
                f" x {_TP_FEASIBILITY_EFFICIENCY} efficiency)"
            )
        return None

    @staticmethod
    def _phase_affinity_gate(
        signal: IndiaSignal,
        ctx: IndiaContext,
        session_state: SessionState,
        now: datetime,
    ) -> str | None:
        """Doctrine §3 ∧ measured evidence: suppress a setup FAMILY in a
        session PHASE only when the pair is on the owner-approved blocklist
        (INDIA_PHASE_BLOCKLIST — populated from edge-matrix cohorts with
        n ≥ min-sample AND negative net EV, never assumption alone).
        Default OFF and empty — a targeted blocklist, not a time curfew."""
        if not config.PHASE_GATE_ENABLED or not _PHASE_BLOCKLIST:
            return None
        family = SETUP_FAMILY.get(signal.setup_class, SetupFamily.NEUTRAL)
        phase = classify_session_phase(ctx.scan_time_ist)
        if (family, phase) in _PHASE_BLOCKLIST:
            return (
                f"{family} setups blocked in {phase}"
                f" (phase-affinity blocklist, measured-negative cohort)"
            )
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

def _signed_extension(
    entry: float, anchor: float, atr: float, direction: str
) -> float:
    """Signed distance of *entry* from *anchor* in ATRs — positive when the
    entry sits beyond the anchor in the trade's direction (i.e. the trade is
    chasing an extended move), negative when it enters on the cheap side.
    0.0 when either reference is unavailable."""
    if anchor <= 0 or atr <= 0 or entry <= 0:
        return 0.0
    raw = (entry - anchor) / atr
    return round(raw if direction == Direction.LONG else -raw, 3)


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
        # Whole-market direction transition tracker (Session 21): when the
        # label last changed, so each emitted signal can carry its bias age
        # (a bias that latched 5 minutes ago is a different trade from one
        # 3 hours old). Reset with the day.
        self._md_label: str | None = None
        self._md_since: datetime | None = None
        # Per-evaluator exception counter (Session 21). The blanket except
        # around evaluate() keeps one broken evaluator from killing the scan,
        # but before this counter a persistently-raising evaluator was
        # silently absent with only a debug-level trail. Surfaced via the
        # pulse payload — never silence a detected problem.
        self._eval_errors: Counter[str] = Counter()

    @property
    def gates(self) -> GateChain:
        return self._gates

    def rehydrate(self, rows: list[dict], now: datetime) -> None:
        """Restore today's emission state after a restart (see GateChain)."""
        self._gates.rehydrate(rows, now)

    def set_edge_index(self, index: dict) -> None:
        """Install the session's measured-edge lookup into the scorer (loaded
        once at session open — see main.py)."""
        self._scorer.set_edge_index(index)

    def set_allocator_suppress(self, cohorts: frozenset[str]) -> None:
        """Install the session's allocator SUPPRESS set (see GateChain)."""
        self._gates.set_allocator_suppress(cohorts)

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
        # Shadow v2 biases (Session 21) — stamped per signal, never gated on.
        index_biases_v2 = {
            base: dependency.market_bias_v2(ctx)
            for base, ctx in contexts.items()
            if base in config.INDEX_BASES
        }
        for base, ctx in contexts.items():
            for proxy in dependency.proxy_candidates(base):
                bias = index_biases.get(proxy)
                if bias is not None:
                    ctx.index_bias = bias
                    break

        # The market-wide read for this scan (session phase, VIX regime, PCR,
        # market direction) — folded once from the index contexts and stamped
        # onto every signal below so outcomes can be sliced by tape regime.
        market_ctx = MarketContext.build(
            {b: c for b, c in contexts.items() if b in config.INDEX_BASES}, now
        )
        if market_ctx.market_direction != self._md_label:
            self._md_label = market_ctx.market_direction
            self._md_since = now
        bias_age_min = (
            (now - self._md_since).total_seconds() / 60.0
            if self._md_since is not None
            else -1.0
        )
        for ctx in contexts.values():
            ctx.market_direction = market_ctx.market_direction

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
                    self._eval_errors[ev.setup_class] += 1
                    logger.opt(exception=True).warning(
                        "evaluator {} raised on {} ({} errors today)",
                        ev.setup_class,
                        base,
                        self._eval_errors[ev.setup_class],
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
                # Scoring v2 (Session 21): computed for every scored
                # candidate; SHADOW persists it for the calibration window,
                # ACTIVE (owner sign-off) makes it the confidence that
                # feeds the floor, tiering, and delivery. v1 always rides
                # in the component JSON for rollback comparison.
                if config.SCORING_V2_SHADOW or config.SCORING_V2_ACTIVE:
                    v2, comps = self._scorer.score_v2(
                        candidate,
                        ctx,
                        session_phase=market_ctx.session_phase,
                        bias_age_min=bias_age_min,
                        dup_index=self._gates.emitted_count(
                            base, candidate.direction
                        )
                        + 1,
                    )
                    comps["v1"] = round(confidence, 2)
                    candidate.confidence_v2 = v2
                    candidate.score_components_v2 = json.dumps(comps)
                    if config.SCORING_V2_ACTIVE:
                        confidence = v2
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
            candidate.pcr_at_entry = ctx.pcr
            candidate.market_direction = market_ctx.market_direction
            candidate.session_phase = market_ctx.session_phase
            candidate.vix_regime = market_ctx.vix_regime
            candidate.expiry_date = self._expiry.get_contract_expiry_date(now)
            candidate.days_to_expiry = self._expiry.days_to_expiry(now)
            # Truth telemetry (Session 21): signed extension of entry from
            # the session anchors (in ATRs, positive = extended in the
            # signal's direction), bias freshness, duplicate ordinal. These
            # are the exhaustion/churn dimensions the edge matrix slices —
            # measured first, gated later only with evidence.
            candidate.extension_vwap_atr = _signed_extension(
                candidate.entry, ctx.session_vwap, ctx.atr14_5m,
                candidate.direction,
            )
            candidate.extension_ema21_atr = _signed_extension(
                candidate.entry, ctx.ema21_5m, ctx.atr14_5m,
                candidate.direction,
            )
            candidate.bias_age_min = round(bias_age_min, 1)
            candidate.dup_index = (
                self._gates.emitted_count(ctx.base, candidate.direction) + 1
            )
            # Shadow direction v2 (never gated on until the forward window
            # proves it beats v1): whole-market label + this base's proxy
            # index bias under the de-lagged classifier.
            candidate.market_direction_v2 = market_ctx.market_direction_v2
            candidate.index_bias_v2 = next(
                (
                    index_biases_v2[p]
                    for p in dependency.proxy_candidates(ctx.base)
                    if p in index_biases_v2
                ),
                dependency.NEUTRAL,
            )

            self._gates.record_emission(
                candidate.setup_class,
                ctx.base,
                candidate.direction,
                now,
                entry=candidate.entry,
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

    @property
    def evaluator_errors(self) -> dict[str, int]:
        """Exceptions swallowed per evaluator today (pulse/ops surface)."""
        return dict(self._eval_errors)

    def reset_day(self) -> None:
        self._gates.reset_day()
        self._scan_count = 0
        self._md_label = None
        self._md_since = None
        self._eval_errors.clear()
