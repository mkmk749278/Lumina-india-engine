"""Setup evaluators (spec §10).

Each evaluator owns its SL/TP geometry (CLAUDE.md). This module lands them one
at a time; the first is LIQUIDITY_SWEEP_REVERSAL (§10.1). All geometry constants
are env-overridable via ``config``.
"""

from __future__ import annotations

import uuid

import config
from src.channels.base import Evaluator
from src.market.candle import Candle
from src.regime import Regime
from src.signals.model import Direction, IndiaContext, IndiaSignal, SetupClass
from src.structure_state import last_swing_high, last_swing_low


class LiquiditySweepReversal(Evaluator):
    """§10.1 — a 15m swing high/low is swept and reclaimed on the 5m candle.

    LONG when a swing LOW is swept (wick through, body closes back above); SHORT
    when a swing HIGH is swept. The sweep bar must carry above-average volume.
    """

    setup_class = SetupClass.LIQUIDITY_SWEEP_REVERSAL

    def evaluate(self, ctx: IndiaContext) -> IndiaSignal | None:
        if not self.enabled:
            return None
        if len(ctx.candles_5m) < 2 or len(ctx.candles_15m) < 3:
            return None
        if ctx.volume_avg_5m_20 <= 0:
            return None

        sweep = ctx.candles_5m[-1]
        if sweep.volume < config.LSR_VOLUME_MULT * ctx.volume_avg_5m_20:
            return None

        swing_low = last_swing_low(
            ctx.candles_15m, lookback=config.LSR_SWING_LOOKBACK, width=1
        )
        swing_high = last_swing_high(
            ctx.candles_15m, lookback=config.LSR_SWING_LOOKBACK, width=1
        )

        if swing_low is not None and sweep.low < swing_low and sweep.close > swing_low:
            return self._build(ctx, Direction.LONG, sweep, tp_level=swing_high)
        if swing_high is not None and sweep.high > swing_high and sweep.close < swing_high:
            return self._build(ctx, Direction.SHORT, sweep, tp_level=swing_low)
        return None

    def _build(
        self,
        ctx: IndiaContext,
        direction: str,
        sweep: Candle,
        tp_level: float | None,
    ) -> IndiaSignal | None:
        entry = sweep.close
        atr_pad = ctx.atr14_5m * config.LSR_SL_ATR_MULT
        if direction == Direction.LONG:
            sl = min(sweep.low - atr_pad, sweep.low - ctx.tick_size)
        else:
            sl = max(sweep.high + atr_pad, sweep.high + ctx.tick_size)

        sl_dist = abs(entry - sl)
        if entry <= 0 or sl_dist <= 0:
            return None
        sl_pct = sl_dist / entry * 100.0
        if not (config.LSR_MIN_SL_PCT <= sl_pct <= config.LSR_MAX_SL_PCT):
            return None

        tp1 = self._take_profit(direction, entry, sl_dist, tp_level, sl_pct)
        tp1_pct = abs(tp1 - entry) / entry * 100.0
        rr = tp1_pct / sl_pct
        if rr < config.LSR_MIN_RR:
            return None

        lot = config.INSTRUMENTS[ctx.base].lot_size if ctx.base in config.INSTRUMENTS else 0
        return IndiaSignal(
            signal_id=str(uuid.uuid4()),
            symbol=ctx.symbol,
            base=ctx.base,
            direction=direction,
            setup_class=self.setup_class,
            entry=entry,
            sl=sl,
            tp1=tp1,
            sl_pct=sl_pct,
            tp1_pct=tp1_pct,
            rr_ratio=rr,
            lot_size=lot,
            htf_trend_aligned=self._htf_aligned(direction, ctx.regime_60m),
            regime_60m=ctx.regime_60m,
            regime_daily=ctx.regime_daily,
            atr_at_entry=ctx.atr14_5m,
            vix_at_entry=ctx.india_vix,
            setup_reason=(
                "15m swing-low sweep + reclaim"
                if direction == Direction.LONG
                else "15m swing-high sweep + reclaim"
            ),
        )

    @staticmethod
    def _take_profit(
        direction: str,
        entry: float,
        sl_dist: float,
        tp_level: float | None,
        sl_pct: float,
    ) -> float:
        """Next-swing target if it clears the min-R:R floor, else a 2R fallback."""
        fallback = (
            entry + sl_dist * 2.0 if direction == Direction.LONG else entry - sl_dist * 2.0
        )
        if direction == Direction.LONG:
            if tp_level is None or tp_level <= entry:
                return fallback
            candidate = tp_level
        else:
            if tp_level is None or tp_level >= entry:
                return fallback
            candidate = tp_level
        candidate_pct = abs(candidate - entry) / entry * 100.0
        return candidate if candidate_pct >= sl_pct * config.LSR_MIN_RR else fallback

    @staticmethod
    def _htf_aligned(direction: str, regime: Regime) -> bool:
        if direction == Direction.LONG:
            return regime in (Regime.TRENDING_UP, Regime.RANGING)
        return regime in (Regime.TRENDING_DOWN, Regime.RANGING)
