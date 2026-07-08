"""Confidence scoring (spec §11) — the 8-component, 0–100 model.

OWNER-SIGN-OFF ITEM (CLAUDE.md): this is the evaluator scoring model. It is a
faithful port of spec §11.2–11.9, adapted to the engine's typed ``Candle`` list
(the spec's pseudocode assumed pandas). No auto-merge.

Components (max): regime 20, htf 15, volume 15, rr 15, level-confluence 10,
oi 10, vix/pcr 10, structure 5 → 100. Emit >= floor; A+ >= A-plus cutoff.
"""

from __future__ import annotations

import config
from src.regime import Regime
from src.signals.model import Direction, IndiaContext, IndiaSignal, SetupClass, Tier

# setup_class -> (aligned, neutral, opposing) regime affinity points.
REGIME_AFFINITY: dict[str, tuple[int, int, int]] = {
    SetupClass.LIQUIDITY_SWEEP_REVERSAL: (18, 12, 8),
    SetupClass.OPENING_RANGE_BREAKOUT: (16, 14, 8),
    SetupClass.TREND_PULLBACK_EMA: (20, 8, 0),
    SetupClass.VOLUME_SURGE_BREAKOUT: (14, 14, 10),
    SetupClass.BREAKDOWN_SHORT: (14, 14, 10),
    SetupClass.SR_FLIP_RETEST: (16, 12, 8),
    SetupClass.INDIA_VIX_EXTREME: (10, 10, 10),
    SetupClass.PCR_EXTREME: (10, 10, 10),
    SetupClass.FAILED_AUCTION_RECLAIM: (18, 12, 8),
    SetupClass.DIVERGENCE_CONTINUATION: (16, 12, 8),
    SetupClass.QUIET_COMPRESSION_BREAK: (14, 14, 10),
    SetupClass.MA_CROSS_TREND_SHIFT: (16, 14, 0),
    SetupClass.OI_SPIKE_REVERSAL: (14, 12, 8),
    SetupClass.EXPIRY_GAMMA_SQUEEZE: (12, 12, 12),
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


class IndiaSignalScoringEngine:
    """Scores a gate-passing candidate 0–100 and assigns its tier."""

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
        )
        return min(100.0, max(0.0, total))

    @staticmethod
    def _score_regime(signal: IndiaSignal, ctx: IndiaContext) -> float:
        aligned, neutral, opposing = REGIME_AFFINITY.get(signal.setup_class, (14, 12, 8))
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
            return 15.0
        if signal.direction == Direction.LONG and ctx.regime_daily is Regime.TRENDING_UP:
            return 10.0
        if signal.direction == Direction.SHORT and ctx.regime_daily is Regime.TRENDING_DOWN:
            return 10.0
        if ctx.regime_daily in _RANGING_QUIET:
            return 8.0
        return 4.0

    @staticmethod
    def _score_volume(signal: IndiaSignal, ctx: IndiaContext) -> float:
        if signal.setup_class in _BREAKOUT_SETUPS and signal.breakout_volume_ratio > 0:
            ratio = signal.breakout_volume_ratio
        elif ctx.candles_5m and ctx.volume_avg_5m_20 > 0:
            ratio = ctx.candles_5m[-1].volume / ctx.volume_avg_5m_20
        else:
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
        rr = signal.rr_ratio
        if rr >= 3.0:
            return 15.0
        if rr >= 2.5:
            return 13.0
        if rr >= 2.0:
            return 11.0
        if rr >= 1.8:
            return 9.0
        if rr >= 1.5:
            return 7.0
        return 4.0

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
        ]
        confluences = sum(
            1 for lvl in key_levels if lvl is not None and abs(entry - lvl) <= tolerance
        )
        if confluences >= 3:
            return 10.0
        if confluences == 2:
            return 8.0
        if confluences == 1:
            return 5.0
        return 2.0

    @staticmethod
    def _score_oi(signal: IndiaSignal, ctx: IndiaContext) -> float:
        oi_chg = ctx.oi_change_15m_pct
        if len(ctx.candles_5m) >= 3:
            ref = ctx.candles_5m[-3].close
            price_rising = ref != 0 and (ctx.candles_5m[-1].close - ref) > 0
        else:
            price_rising = False
        oi_rising = oi_chg > 0.5
        if signal.direction == Direction.LONG and oi_rising and price_rising:
            return 10.0
        if signal.direction == Direction.SHORT and oi_rising and not price_rising:
            return 10.0
        if oi_chg > 0.2:
            return 7.0
        if abs(oi_chg) < 0.2:
            return 5.0
        return 3.0

    @staticmethod
    def _score_vix_pcr(signal: IndiaSignal, ctx: IndiaContext) -> float:
        score = 5.0
        # vix == 0.0 means "no VIX reading yet", not "record-low vol" — no bonus.
        if 0 < ctx.india_vix < 14 and signal.setup_class in _LOW_VIX_FAVOURED:
            score += 2.0
        elif ctx.india_vix > 18 and signal.setup_class in _HIGH_VIX_FAVOURED:
            score += 2.0
        if signal.direction == Direction.LONG and ctx.pcr_is_extreme_bearish:
            score += 3.0
        if signal.direction == Direction.SHORT and ctx.pcr_is_extreme_bullish:
            score += 3.0
        if signal.direction == Direction.SHORT and ctx.pcr_is_extreme_bearish:
            score -= 2.0
        return min(10.0, max(0.0, score))

    @staticmethod
    def _score_structure(signal: IndiaSignal, ctx: IndiaContext) -> float:
        # Volatility-normality score: ATR as % of price against the typical
        # 5m band for the instrument class (indices run far tighter than
        # single stocks). Absolute-point baselines only made sense for the
        # two original indices.
        last = ctx.candles_5m[-1].close if ctx.candles_5m else 0.0
        if last <= 0:
            return 2.0
        atr_pct = ctx.atr14_5m / last * 100.0
        baseline = 0.035 if ctx.base in config.INDEX_BASES else 0.12
        atr_ratio = atr_pct / baseline
        if atr_ratio < 0.5:
            return 2.0
        if atr_ratio > 3.0:
            return 1.0
        if 0.8 <= atr_ratio <= 2.0:
            return 5.0
        return 3.0


def tier_for(confidence: float) -> str:
    """Map a confidence score to its delivery tier (spec §13.1)."""
    if confidence >= config.CONFIDENCE_A_PLUS:
        return Tier.A_PLUS
    if confidence >= config.CONFIDENCE_EMIT_FLOOR:
        return Tier.B
    return Tier.FILTERED
