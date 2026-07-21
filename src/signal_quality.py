"""Confidence scoring — the 9-component, 0–100 model.

OWNER-SIGN-OFF ITEM (CLAUDE.md): this is the evaluator scoring model. No
auto-merge. Rebuilt in the signal-quality overhaul (owner-directed) from the
spec §11 8-component port:

Component budget (max, total 100):
  regime alignment   15  (was 20 — regime now needs real EMA separation, so a
                          trend label is rarer and more reliable; 20 let one
                          input dominate)
  HTF (daily)        12  (was 15)
  volume             15  (now time-of-day normalised + building-bar pro-rated)
  risk:reward        15  (net of round-trip cost — unchanged from Session 11)
  level confluence   10  (now also counts unmitigated order blocks / FVGs)
  OI positioning     10  (proper 4-quadrant buildup matrix, direction-aware)
  VIX / PCR           8  (was 10)
  market structure   10  (was 5 — now scores actual BOS/CHoCH alignment on 15m
                          plus ATR normality; before it was ATR normality only
                          and the structure_state module was never consumed)
  index alignment     5  (new — dependency pairs: signal direction vs the
                          proxy index's intraday bias)

Emit >= floor; A+ >= A-plus cutoff (config).
"""

from __future__ import annotations

import config
from src.dependency import NEUTRAL
from src.order_blocks import find_fvgs, find_order_blocks, unmitigated
from src.regime import Regime
from src.signals.model import (
    SETUP_FAMILY,
    Direction,
    IndiaContext,
    IndiaSignal,
    SetupClass,
    SetupFamily,
    Tier,
)
from src.structure_state import StructureEvent, last_structure_event

# How many recent 15m bars a BOS/CHoCH stays live as structure bias.
STRUCTURE_LOOKBACK: int = config._safe_int("INDIA_STRUCTURE_LOOKBACK", 12)
# 15m window mined for unmitigated order blocks / FVGs (one session ≈ 25 bars;
# zones older than ~1.5 sessions are stale as intraday confluence).
ZONE_WINDOW_BARS: int = config._safe_int("INDIA_ZONE_WINDOW_BARS", 40)

# setup_class -> (aligned, neutral, opposing) regime affinity points (max 15).
REGIME_AFFINITY: dict[str, tuple[int, int, int]] = {
    SetupClass.LIQUIDITY_SWEEP_REVERSAL: (13, 9, 6),
    SetupClass.OPENING_RANGE_BREAKOUT: (12, 10, 6),
    SetupClass.TREND_PULLBACK_EMA: (15, 6, 0),
    SetupClass.VOLUME_SURGE_BREAKOUT: (11, 10, 8),
    SetupClass.BREAKDOWN_SHORT: (11, 10, 8),
    SetupClass.SR_FLIP_RETEST: (12, 9, 6),
    SetupClass.INDIA_VIX_EXTREME: (8, 8, 8),
    SetupClass.PCR_EXTREME: (8, 8, 8),
    SetupClass.FAILED_AUCTION_RECLAIM: (13, 9, 6),
    SetupClass.DIVERGENCE_CONTINUATION: (12, 9, 6),
    SetupClass.QUIET_COMPRESSION_BREAK: (11, 10, 8),
    SetupClass.MA_CROSS_TREND_SHIFT: (12, 10, 0),
    SetupClass.OI_SPIKE_REVERSAL: (11, 9, 6),
    SetupClass.EXPIRY_GAMMA_SQUEEZE: (9, 9, 9),
}

_BREAKOUT_SETUPS = {
    SetupClass.VOLUME_SURGE_BREAKOUT,
    SetupClass.BREAKDOWN_SHORT,
    SetupClass.OPENING_RANGE_BREAKOUT,
}
_LOW_VIX_FAVOURED = {
    SetupClass.OPENING_RANGE_BREAKOUT,
    SetupClass.TREND_PULLBACK_EMA,
    SetupClass.VOLUME_SURGE_BREAKOUT,
    SetupClass.BREAKDOWN_SHORT,
    SetupClass.MA_CROSS_TREND_SHIFT,
}
_HIGH_VIX_FAVOURED = {
    SetupClass.LIQUIDITY_SWEEP_REVERSAL,
    SetupClass.INDIA_VIX_EXTREME,
    SetupClass.PCR_EXTREME,
    SetupClass.FAILED_AUCTION_RECLAIM,
    SetupClass.OI_SPIKE_REVERSAL,
}

_RANGING_QUIET = (Regime.RANGING, Regime.QUIET)

# ── scoring v2 (Session 21 — SHADOW; tiers stay on v1 until
#    INDIA_SCORING_V2_ACTIVE flips after the forward window) ──────────
#
# The live ledger proved v1 anti-predictive (conf 55-60 → 33.8% win,
# 75+ → 19.4%; tier A −5.29% net vs tier B +8.93%). Root cause: four
# components — regime 15, HTF 12, structure-BOS 7, index 5 (≤39/100) —
# all restate the same fact, "the trend is fully established", which is
# empirically the *late/exhausted* condition. v2:
#   • collapses those four into ONE trend_establishment component (≤15,
#     the strongest single read counts, restatements don't stack)
#   • prices in extension (0..−10): established trend + price stretched
#     beyond VWAP is precisely the measured losing cohort
#   • prices in session-phase affinity (≤8, doctrine §3: breakouts pay in
#     the power hour, reversion midday, trend-continuation into the close)
#   • prices in freshness (≤7): a bias that latched minutes ago plus a
#     first-of-day signal beats a stale bias / third duplicate
# Volume/RR/confluence/OI/VIX-PCR and the measured-edge nudge are
# unchanged — the ledger showed no inversion in those.

# family -> phase -> points (max 8). Env-overridable via
# INDIA_PHASE_AFFINITY as CSV "FAMILY:PHASE:PTS" entries.
_DEFAULT_PHASE_AFFINITY: dict[str, dict[str, float]] = {
    SetupFamily.BREAKOUT: {
        "POWER_HOUR": 8.0, "MIDDAY_CHOP": 2.0, "CLOSING": 5.0,
    },
    SetupFamily.REVERSION: {
        "POWER_HOUR": 3.0, "MIDDAY_CHOP": 8.0, "CLOSING": 5.0,
    },
    SetupFamily.TREND: {
        "POWER_HOUR": 5.0, "MIDDAY_CHOP": 2.0, "CLOSING": 8.0,
    },
    SetupFamily.NEUTRAL: {
        "POWER_HOUR": 5.0, "MIDDAY_CHOP": 5.0, "CLOSING": 5.0,
    },
}


def _phase_affinity_table() -> dict[str, dict[str, float]]:
    table = {fam: dict(row) for fam, row in _DEFAULT_PHASE_AFFINITY.items()}
    raw = config._safe_str("INDIA_PHASE_AFFINITY", "")
    for entry in raw.split(","):
        parts = entry.strip().split(":")
        if len(parts) != 3:
            continue
        fam, phase, pts = parts[0].upper(), parts[1].upper(), parts[2]
        try:
            table.setdefault(fam, {})[phase] = float(pts)
        except ValueError:
            continue
    return table


_PHASE_AFFINITY = _phase_affinity_table()


class IndiaSignalScoringEngine:
    """Scores a gate-passing candidate 0–100 and assigns its tier."""

    def __init__(self) -> None:
        # (setup_class, direction) -> {n, ev_net_pct}, loaded once per session
        # open (src/strategy_edge.get_edge_index). Empty = no measured edge yet
        # → the adjustment is inert (exact prior scoring).
        self._edge_index: dict[tuple[str, str], dict] = {}

    def set_edge_index(self, index: dict[tuple[str, str], dict]) -> None:
        """Install the session's measured-edge lookup (cached; not per-scan)."""
        self._edge_index = index or {}

    def score(self, signal: IndiaSignal, ctx: IndiaContext) -> float:
        total = (
            self._score_regime(signal, ctx)
            + self._score_htf(signal, ctx)
            + self._score_volume(signal, ctx)
            + self._score_rr(signal)
            + self._score_level_confluence(signal, ctx)
            + self._score_oi(signal, ctx)
            + self._score_vix_pcr(signal, ctx)
            + self._score_structure(signal, ctx)
            + self._score_index_alignment(signal, ctx)
            + self._score_measured_edge(signal)
        )
        return min(100.0, max(0.0, total))

    def _score_measured_edge(self, signal: IndiaSignal) -> float:
        """Bounded ± nudge toward the candidate cohort's *measured* cost-adjusted
        expectancy — the honest, non-overfit tier recalibration. Inert unless
        the (setup, direction) cohort has ≥ ALLOCATOR_MIN_SAMPLE resolved trades,
        so a thin/one-day sample changes nothing; capped at ±EDGE_ADJUST_CAP so a
        single cohort can never dominate the 0–100 budget."""
        if not config.EDGE_ADJUST_ENABLED:
            return 0.0
        cell = self._edge_index.get((signal.setup_class, signal.direction))
        if cell is None or int(cell.get("n", 0)) < config.ALLOCATOR_MIN_SAMPLE:
            return 0.0
        raw = config.EDGE_ADJUST_K * float(cell.get("ev_net_pct", 0.0))
        cap = config.EDGE_ADJUST_CAP
        return max(-cap, min(cap, raw))

    def score_v2(
        self,
        signal: IndiaSignal,
        ctx: IndiaContext,
        *,
        session_phase: str = "",
        bias_age_min: float = -1.0,
        dup_index: int = 1,
    ) -> tuple[float, dict[str, float]]:
        """The rebuilt scoring model (see module notes above). Returns
        (score, components) — components are persisted as JSON so the ops
        calibration panel can show WHY v2 disagrees with v1 per signal."""
        comps: dict[str, float] = {
            "trend_establishment": self._v2_trend_establishment(signal, ctx),
            "atr_normality": self._v2_atr_normality(ctx),
            "volume": self._score_volume(signal, ctx),
            "rr": self._score_rr(signal),
            "confluence": self._score_level_confluence(signal, ctx),
            "oi": self._score_oi(signal, ctx),
            "vix_pcr": self._score_vix_pcr(signal, ctx),
            "phase_affinity": self._v2_phase_affinity(signal, session_phase),
            "freshness": self._v2_freshness(signal, ctx, bias_age_min),
            "dup_penalty": -3.0 * max(0, min(2, dup_index - 1)),
            "extension_penalty": self._v2_extension_penalty(signal, ctx),
            "measured_edge": self._score_measured_edge(signal),
        }
        total = min(100.0, max(0.0, sum(comps.values())))
        comps = {k: round(v, 2) for k, v in comps.items()}
        return round(total, 2), comps

    @staticmethod
    def _v2_trend_establishment(signal: IndiaSignal, ctx: IndiaContext) -> float:
        """ONE component (≤15) for 'is the tape aligned with this trade' —
        the strongest of the four v1 reads, never their sum. Four
        restatements of one fact were worth ≤39 points in v1 and drove the
        tier inversion."""
        aligned, neutral_pts, opposing = REGIME_AFFINITY.get(
            signal.setup_class, (11, 9, 6)
        )
        long = signal.direction == Direction.LONG
        # Subscores in [0, 1].
        if ctx.regime_60m is (Regime.TRENDING_UP if long else Regime.TRENDING_DOWN):
            regime_sub = aligned / 15.0
        elif ctx.regime_60m in _RANGING_QUIET:
            regime_sub = neutral_pts / 15.0
        else:
            regime_sub = opposing / 15.0
        htf_sub = (
            1.0
            if signal.htf_trend_aligned
            or ctx.regime_daily
            is (Regime.TRENDING_UP if long else Regime.TRENDING_DOWN)
            else 0.4
        )
        event = (
            last_structure_event(
                ctx.candles_15m, width=2, lookback=STRUCTURE_LOOKBACK
            )
            if len(ctx.candles_15m) >= 7
            else None
        )
        if event is None:
            structure_sub = 0.4
        elif event is (StructureEvent.BOS_UP if long else StructureEvent.BOS_DOWN):
            structure_sub = 1.0
        elif event is (
            StructureEvent.CHOCH_UP if long else StructureEvent.CHOCH_DOWN
        ):
            structure_sub = 0.7
        else:
            structure_sub = 0.0
        index_sub = (
            0.6
            if ctx.index_bias == NEUTRAL
            else (1.0 if ctx.index_bias == signal.direction else 0.0)
        )
        return 15.0 * max(regime_sub, htf_sub, structure_sub, index_sub)

    @staticmethod
    def _v2_atr_normality(ctx: IndiaContext) -> float:
        """The 0-3 ATR-normality slice of v1's structure component, kept as
        its own small component (it measures tape health, not trend)."""
        last = ctx.candles_5m[-1].close if ctx.candles_5m else 0.0
        if last <= 0:
            return 1.0
        atr_pct = ctx.atr14_5m / last * 100.0
        baseline = 0.035 if ctx.base in config.INDEX_BASES else 0.12
        atr_ratio = atr_pct / baseline
        if atr_ratio > 3.0:
            return 0.0
        if 0.8 <= atr_ratio <= 2.0:
            return 3.0
        if atr_ratio < 0.5:
            return 1.0
        return 2.0

    @staticmethod
    def _v2_phase_affinity(signal: IndiaSignal, session_phase: str) -> float:
        """Doctrine §3: the same setup family flips sign by session phase.
        Unknown phase reads the neutral midpoint (4.0) — never fabricated."""
        family = SETUP_FAMILY.get(signal.setup_class, SetupFamily.NEUTRAL)
        row = _PHASE_AFFINITY.get(family, {})
        return row.get(str(session_phase).upper(), 4.0)

    @staticmethod
    def _v2_freshness(
        signal: IndiaSignal, ctx: IndiaContext, bias_age_min: float
    ) -> float:
        """A directional read that latched recently is worth more than one
        that has been latched for hours (the stale label is the exhausted
        one). Neutral tape or unknown age reads the midpoint."""
        if ctx.market_direction == "NEUTRAL" or bias_age_min < 0:
            return 3.5
        aligned = (
            ctx.market_direction == "LONG_BIASED"
            and signal.direction == Direction.LONG
        ) or (
            ctx.market_direction == "SHORT_BIASED"
            and signal.direction == Direction.SHORT
        )
        if not aligned:
            return 3.5
        if bias_age_min <= 30.0:
            return 7.0
        if bias_age_min <= 120.0:
            return 5.0
        return 2.0

    @staticmethod
    def _v2_extension_penalty(signal: IndiaSignal, ctx: IndiaContext) -> float:
        """0 to −10 as entry stretches beyond session VWAP in the trade's
        direction. Established trend + extended price is precisely the
        measured losing cohort (LONG_BIASED/LONG 6.2% win); this is the
        counterweight that fixes the inversion instead of shrinking it."""
        if ctx.session_vwap <= 0 or ctx.atr14_5m <= 0 or signal.entry <= 0:
            return 0.0
        raw = (signal.entry - ctx.session_vwap) / ctx.atr14_5m
        ext = raw if signal.direction == Direction.LONG else -raw
        if ext <= 0.5:
            return 0.0
        return -min(10.0, (ext - 0.5) * 5.0)

    @staticmethod
    def _score_regime(signal: IndiaSignal, ctx: IndiaContext) -> float:
        aligned, neutral, opposing = REGIME_AFFINITY.get(signal.setup_class, (11, 9, 6))
        regime = ctx.regime_60m
        if signal.direction == Direction.LONG:
            if regime is Regime.TRENDING_UP:
                return aligned
            if regime in _RANGING_QUIET:
                return neutral
            return opposing
        if regime is Regime.TRENDING_DOWN:
            return aligned
        if regime in _RANGING_QUIET:
            return neutral
        return opposing

    @staticmethod
    def _score_htf(signal: IndiaSignal, ctx: IndiaContext) -> float:
        if signal.htf_trend_aligned:
            return 12.0
        if signal.direction == Direction.LONG and ctx.regime_daily is Regime.TRENDING_UP:
            return 8.0
        if signal.direction == Direction.SHORT and ctx.regime_daily is Regime.TRENDING_DOWN:
            return 8.0
        if ctx.regime_daily in _RANGING_QUIET:
            return 6.0
        return 3.0

    @staticmethod
    def _score_volume(signal: IndiaSignal, ctx: IndiaContext) -> float:
        # Ratios are time-of-day normalised where the context provides it
        # (src/market_profile.py): 1.0 = normal *for this session phase*, so a
        # midday 1.5 is a real surge and an opening 1.5 is just the open.
        if signal.setup_class in _BREAKOUT_SETUPS and signal.breakout_volume_ratio > 0:
            ratio = signal.breakout_volume_ratio
        else:
            ratio = ctx.current_volume_ratio()
            if ratio <= 0:
                return 8.0
        if ratio >= 3.0:
            return 15.0
        if ratio >= 2.0:
            return 13.0
        if ratio >= 1.5:
            return 11.0
        if ratio >= 1.2:
            return 9.0
        if ratio >= 1.0:
            return 8.0
        return 5.0

    @staticmethod
    def _score_rr(signal: IndiaSignal) -> float:
        # Score the reward the subscriber actually keeps, not the gross target.
        # A scalp that clears STT but nets ~nothing is not a 2R trade. After the
        # Apr-2026 STT hike (futures sell-side 0.02% -> 0.05%) the all-in
        # round-trip cost is ~0.06% of notional — material on a scalp — so net
        # R:R = (target - cost) / (stop + cost). This favours cost-efficient,
        # larger-target setups over thin scalps with the same gross R:R.
        # signal.rr_ratio stays gross (geometry/display contract unchanged); the
        # bands below are recentred for net R:R (a net 1.3 is a genuinely good
        # post-cost trade). See config.round_trip_cost_points.
        cost = config.round_trip_cost_points(signal.entry)
        net_reward = abs(signal.tp1 - signal.entry) - cost
        net_risk = abs(signal.entry - signal.sl) + cost
        rr = net_reward / net_risk if net_risk > 0 else 0.0
        if rr >= 2.0:
            return 15.0
        if rr >= 1.6:
            return 13.0
        if rr >= 1.3:
            return 11.0
        if rr >= 1.0:
            return 9.0
        if rr >= 0.7:
            return 6.0
        return 3.0

    @staticmethod
    def _score_level_confluence(signal: IndiaSignal, ctx: IndiaContext) -> float:
        entry = signal.entry
        tolerance = ctx.atr14_5m * 0.5
        step = config.round_step_for(ctx.base, entry)
        base_round = round(entry / step) * step
        key_levels: list[float | None] = [
            ctx.opening_range_high,
            ctx.opening_range_low,
            ctx.prev_day_high,
            ctx.prev_day_low,
            ctx.prev_day_close,
            base_round - step,
            base_round,
            base_round + step,
            *ctx.key_levels_extra,
            # Option-chain OI walls + max-pain are first-class NSE index S/R
            # (index bases only; None on stocks / before the first chain poll).
            ctx.call_oi_wall,
            ctx.put_oi_wall,
            ctx.max_pain_strike,
        ]
        confluences = sum(
            1 for lvl in key_levels if lvl is not None and abs(entry - lvl) <= tolerance
        )
        # An unmitigated order block / FVG on the 15m whose zone the entry sits
        # in (or within tolerance of) is a genuine institutional footprint —
        # count it as one more confluence. Direction-aware: bullish zones back
        # LONGs, bearish zones back SHORTs.
        if _entry_in_aligned_zone(signal, ctx, tolerance):
            confluences += 1
        if confluences >= 3:
            return 10.0
        if confluences == 2:
            return 8.0
        if confluences == 1:
            return 5.0
        return 2.0

    @staticmethod
    def _score_oi(signal: IndiaSignal, ctx: IndiaContext) -> float:
        """Classic F&O positioning matrix over price change x OI change:

            price up   + OI up   = long buildup    -> backs LONG
            price down + OI up   = short buildup   -> backs SHORT
            price up   + OI down = short covering  -> weakly backs LONG
            price down + OI down = long unwinding  -> weakly backs SHORT

        The old component paid 7/10 for any rising OI regardless of which side
        was building — an OI surge *against* the signal scored nearly as well
        as one confirming it.
        """
        oi_chg = ctx.oi_change_15m_pct
        if len(ctx.candles_5m) >= 3:
            ref = ctx.candles_5m[-3].close
            price_delta = ctx.candles_5m[-1].close - ref if ref != 0 else 0.0
        else:
            price_delta = 0.0

        if abs(oi_chg) < 0.2 or price_delta == 0.0:
            return 5.0

        price_up = price_delta > 0
        oi_up = oi_chg > 0
        if oi_up and price_up:  # long buildup
            return 10.0 if signal.direction == Direction.LONG else 0.0
        if oi_up and not price_up:  # short buildup
            return 10.0 if signal.direction == Direction.SHORT else 0.0
        if not oi_up and price_up:  # short covering
            return 6.0 if signal.direction == Direction.LONG else 3.0
        # long unwinding
        return 6.0 if signal.direction == Direction.SHORT else 3.0

    @staticmethod
    def _score_vix_pcr(signal: IndiaSignal, ctx: IndiaContext) -> float:
        score = 4.0
        # vix == 0.0 means "no VIX reading yet", not "record-low vol" — no bonus.
        if 0 < ctx.india_vix < 14 and signal.setup_class in _LOW_VIX_FAVOURED:
            score += 2.0
        elif ctx.india_vix > 18 and signal.setup_class in _HIGH_VIX_FAVOURED:
            score += 2.0
        if signal.direction == Direction.LONG and ctx.pcr_is_extreme_bearish:
            score += 2.0
        if signal.direction == Direction.SHORT and ctx.pcr_is_extreme_bullish:
            score += 2.0
        if signal.direction == Direction.SHORT and ctx.pcr_is_extreme_bearish:
            score -= 2.0
        if signal.direction == Direction.LONG and ctx.pcr_is_extreme_bullish:
            score -= 2.0
        return min(8.0, max(0.0, score))

    @staticmethod
    def _score_structure(signal: IndiaSignal, ctx: IndiaContext) -> float:
        """BOS/CHoCH alignment on the 15m (0–7) + ATR normality (0–3).

        A live break of structure in the signal's direction is the strongest
        confirmation SMC offers: BOS = continuation the signal rides, CHoCH =
        the first reversal print (slightly less established). A live break
        *against* the signal means it is fading fresh displacement — 0.
        """
        event = (
            last_structure_event(
                ctx.candles_15m, width=2, lookback=STRUCTURE_LOOKBACK
            )
            if len(ctx.candles_15m) >= 7
            else None
        )
        long = signal.direction == Direction.LONG
        if event is None:
            structure = 3.0
        elif event is (StructureEvent.BOS_UP if long else StructureEvent.BOS_DOWN):
            structure = 7.0
        elif event is (StructureEvent.CHOCH_UP if long else StructureEvent.CHOCH_DOWN):
            structure = 5.0
        else:
            structure = 0.0

        # Volatility-normality: ATR as % of price against the typical 5m band
        # for the instrument class. Dead tape and panic tape both trade worse
        # than the middle of the band.
        last = ctx.candles_5m[-1].close if ctx.candles_5m else 0.0
        if last <= 0:
            atr_score = 1.0
        else:
            atr_pct = ctx.atr14_5m / last * 100.0
            baseline = 0.035 if ctx.base in config.INDEX_BASES else 0.12
            atr_ratio = atr_pct / baseline
            if atr_ratio > 3.0:
                atr_score = 0.0
            elif 0.8 <= atr_ratio <= 2.0:
                atr_score = 3.0
            elif atr_ratio < 0.5:
                atr_score = 1.0
            else:
                atr_score = 2.0
        return structure + atr_score

    @staticmethod
    def _score_index_alignment(signal: IndiaSignal, ctx: IndiaContext) -> float:
        """Dependency pairs: signal direction vs the proxy index's intraday
        bias (stamped by the scanner). Fighting the anchor index intraday is
        the single most common way an otherwise clean stock setup fails."""
        if ctx.index_bias == NEUTRAL:
            return 3.0
        return 5.0 if ctx.index_bias == signal.direction else 0.0


def _entry_in_aligned_zone(
    signal: IndiaSignal, ctx: IndiaContext, tolerance: float
) -> bool:
    """True if entry sits in/near an unmitigated 15m OB or FVG backing the
    signal's direction.

    Zones and their mitigation are judged on the bars *before* the current
    one: the signal bar tapping into a fresh zone is the entry itself — it
    must not count as the mitigation that disqualifies the zone.
    """
    window = ctx.candles_15m[-ZONE_WINDOW_BARS:-1]
    if len(window) < 3:
        return False
    zones = unmitigated(
        [*find_order_blocks(window), *find_fvgs(window)], window
    )
    want_bullish = signal.direction == Direction.LONG
    return any(
        z.bullish == want_bullish
        and (z.bottom - tolerance) <= signal.entry <= (z.top + tolerance)
        for z in zones
    )


def tier_for(confidence: float) -> str:
    """Map a confidence score to its delivery tier (spec §13.1, IB14).

    A+ / A / B — the three tiers the business rules and the app colour-code.
    Before Session 15 the A band did not exist in code: everything from the
    emit floor to 79 rendered as B, so the ₹999 plan's "A and B signals"
    promise was unfulfillable.
    """
    if confidence >= config.CONFIDENCE_A_PLUS:
        return Tier.A_PLUS
    if confidence >= config.CONFIDENCE_A:
        return Tier.A
    if confidence >= config.CONFIDENCE_EMIT_FLOOR:
        return Tier.B
    return Tier.FILTERED
