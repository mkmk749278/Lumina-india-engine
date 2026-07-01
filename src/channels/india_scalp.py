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


def _lot_size(base: str) -> int:
    return config.INSTRUMENTS[base].lot_size if base in config.INSTRUMENTS else 0


def _trend_matches(direction: str, regime: Regime) -> bool:
    """Strict trend alignment: the 60m regime must be the matching trend."""
    want = Regime.TRENDING_UP if direction == Direction.LONG else Regime.TRENDING_DOWN
    return regime is want


def _make_signal(
    ctx: IndiaContext,
    setup_class: str,
    direction: str,
    entry: float,
    sl: float,
    tp1: float,
    sl_pct: float,
    tp1_pct: float,
    rr: float,
    *,
    htf: bool,
    vol_ratio: float,
    reason: str,
) -> IndiaSignal:
    return IndiaSignal(
        signal_id=str(uuid.uuid4()),
        symbol=ctx.symbol,
        base=ctx.base,
        direction=direction,
        setup_class=setup_class,
        entry=entry,
        sl=sl,
        tp1=tp1,
        sl_pct=sl_pct,
        tp1_pct=tp1_pct,
        rr_ratio=rr,
        lot_size=_lot_size(ctx.base),
        htf_trend_aligned=htf,
        breakout_volume_ratio=vol_ratio,
        regime_60m=ctx.regime_60m,
        regime_daily=ctx.regime_daily,
        atr_at_entry=ctx.atr14_5m,
        vix_at_entry=ctx.india_vix,
        setup_reason=reason,
    )


class OpeningRangeBreakout(Evaluator):
    """§10.2 — a 5m close breaks the 09:15-09:30 opening range with volume."""

    setup_class = SetupClass.OPENING_RANGE_BREAKOUT

    def evaluate(self, ctx: IndiaContext) -> IndiaSignal | None:
        if not self.enabled or not ctx.candles_5m or ctx.volume_avg_5m_20 <= 0:
            return None
        orh, orl = ctx.opening_range_high, ctx.opening_range_low
        if orh is None or orl is None:
            return None
        current = ctx.candles_5m[-1]
        last_price = current.close
        if last_price <= 0:
            return None
        or_range_pct = (orh - orl) / last_price * 100.0
        if not (config.ORB_MIN_RANGE_PCT <= or_range_pct <= config.ORB_MAX_RANGE_PCT):
            return None
        vol_ratio = current.volume / ctx.volume_avg_5m_20
        if vol_ratio < config.ORB_VOLUME_MULT:
            return None
        buf = ctx.atr14_5m * config.ORB_ATR_BUFFER_MULT
        if current.close > orh + buf:
            return self._build(ctx, Direction.LONG, orh + buf, orl - buf, vol_ratio)
        if current.close < orl - buf:
            return self._build(ctx, Direction.SHORT, orl - buf, orh + buf, vol_ratio)
        return None

    def _build(
        self,
        ctx: IndiaContext,
        direction: str,
        entry: float,
        sl: float,
        vol_ratio: float,
    ) -> IndiaSignal | None:
        sl_dist = abs(entry - sl)
        if entry <= 0 or sl_dist <= 0:
            return None
        sl_pct = sl_dist / entry * 100.0
        if not (config.ORB_MIN_SL_PCT <= sl_pct <= config.ORB_MAX_SL_PCT):
            return None
        tp1 = (
            entry + sl_dist * config.ORB_TP_RR
            if direction == Direction.LONG
            else entry - sl_dist * config.ORB_TP_RR
        )
        tp1_pct = abs(tp1 - entry) / entry * 100.0
        return _make_signal(
            ctx,
            self.setup_class,
            direction,
            entry,
            sl,
            tp1,
            sl_pct,
            tp1_pct,
            tp1_pct / sl_pct,
            htf=_trend_matches(direction, ctx.regime_60m),
            vol_ratio=vol_ratio,
            reason="opening-range breakout",
        )


def _volume_breakout(
    ctx: IndiaContext, direction: str, setup_class: str
) -> IndiaSignal | None:
    """Shared engine for VOLUME_SURGE_BREAKOUT (long) / BREAKDOWN_SHORT (short)."""
    if not ctx.candles_5m or len(ctx.candles_15m) < 3 or ctx.volume_avg_5m_20 <= 0:
        return None
    current = ctx.candles_5m[-1]
    vol_ratio = current.volume / ctx.volume_avg_5m_20
    if vol_ratio < config.VSB_VOLUME_MULT:
        return None
    if ctx.oi_change_15m_pct < config.VSB_OI_MIN_PCT:
        return None
    atr = ctx.atr14_5m
    if direction == Direction.LONG:
        level = last_swing_high(ctx.candles_15m, lookback=config.VSB_SWING_LOOKBACK, width=1)
        if level is None or not (current.high > level and current.close > level):
            return None
        entry = level + atr * config.VSB_ENTRY_ATR_MULT
        base_sl = level - atr * config.VSB_SL_ATR_MULT
        sl = min(current.open, base_sl) if current.open < level else base_sl
    else:
        level = last_swing_low(ctx.candles_15m, lookback=config.VSB_SWING_LOOKBACK, width=1)
        if level is None or not (current.low < level and current.close < level):
            return None
        entry = level - atr * config.VSB_ENTRY_ATR_MULT
        base_sl = level + atr * config.VSB_SL_ATR_MULT
        sl = max(current.open, base_sl) if current.open > level else base_sl

    sl_dist = abs(entry - sl)
    if entry <= 0 or sl_dist <= 0:
        return None
    sl_pct = sl_dist / entry * 100.0
    if not (config.VSB_MIN_SL_PCT <= sl_pct <= config.VSB_MAX_SL_PCT):
        return None
    tp1 = (
        entry + sl_dist * config.VSB_TP_RR
        if direction == Direction.LONG
        else entry - sl_dist * config.VSB_TP_RR
    )
    tp1_pct = abs(tp1 - entry) / entry * 100.0
    reason = (
        "15m swing-high volume breakout"
        if direction == Direction.LONG
        else "15m swing-low volume breakdown"
    )
    return _make_signal(
        ctx,
        setup_class,
        direction,
        entry,
        sl,
        tp1,
        sl_pct,
        tp1_pct,
        tp1_pct / sl_pct,
        htf=_trend_matches(direction, ctx.regime_60m),
        vol_ratio=vol_ratio,
        reason=reason,
    )


class VolumeSurgeBreakout(Evaluator):
    """§10.4 — breakout above a 15m swing high with a volume + OI surge (long)."""

    setup_class = SetupClass.VOLUME_SURGE_BREAKOUT

    def evaluate(self, ctx: IndiaContext) -> IndiaSignal | None:
        if not self.enabled:
            return None
        return _volume_breakout(ctx, Direction.LONG, self.setup_class)


class BreakdownShort(Evaluator):
    """§10.5 — mirror of VSB below a 15m swing low; independently toggled."""

    setup_class = SetupClass.BREAKDOWN_SHORT
    enabled = config.BDS_ENABLED

    def evaluate(self, ctx: IndiaContext) -> IndiaSignal | None:
        if not self.enabled:
            return None
        return _volume_breakout(ctx, Direction.SHORT, self.setup_class)
