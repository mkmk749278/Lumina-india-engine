"""Setup evaluators (spec §10).

Each evaluator owns its SL/TP geometry (CLAUDE.md). This module lands them one
at a time; the first is LIQUIDITY_SWEEP_REVERSAL (§10.1). All geometry constants
are env-overridable via ``config``.
"""

from __future__ import annotations

import uuid

import config
from src.channels.base import Evaluator
from src.indicators import atr as compute_atr
from src.indicators import bollinger, ema, ema_series, rsi
from src.level_book import LevelBook
from src.market.candle import Candle
from src.patterns import (
    is_bearish_rejection,
    is_bullish_rejection,
)
from src.regime import Regime
from src.signals.model import Direction, IndiaContext, IndiaSignal, SetupClass
from src.structure_state import last_swing_high, last_swing_low


def _pattern_bar_ready(ctx: IndiaContext) -> bool:
    """Pattern-triggered paths only judge a near-final 5m bar.

    A sweep-reclaim / rejection seen 40 seconds into a bar routinely un-forms
    by the close: live 2026-07-10 (first clean window) LSR went 1/9 with every
    loss a forming-bar reclaim that evaporated, and one SRF candidate re-fired
    on every 30s scan for 5 minutes straight. A completed bar always
    qualifies (fraction 1.0)."""
    return ctx.bar_elapsed_fraction >= config.PATTERN_BAR_MIN_ELAPSED


def _min_trigger_range(ctx: IndiaContext) -> float:
    """Minimum trigger-bar range (price units) for a rejection/sweep bar to
    count as a real event rather than doji noise."""
    return ctx.atr14_5m * config.MIN_TRIGGER_RANGE_ATR


def _derive_tp2(ctx: IndiaContext, entry: float, tp1: float, direction: str) -> float:
    """Runner target beyond TP1 (owner-directed two-target plan, Session 18).

    The next structural level past TP1 (PDH/PDL/PDC, locked OR, session VWAP)
    when one sits between TP2_LEVEL_MIN_MULT and TP2_LEVEL_MAX_MULT of the TP1
    distance — the nearest such level wins. Otherwise the R-multiple fallback:
    TP2_DIST_MULT x the TP1 distance. 0.0 when TP2 is disabled or the
    geometry is degenerate (the monitor then runs the legacy single-target
    plan for this signal).
    """
    tp1_dist = abs(tp1 - entry)
    if not config.TP2_ENABLED or entry <= 0 or tp1_dist <= 0:
        return 0.0
    lo = tp1_dist * config.TP2_LEVEL_MIN_MULT
    hi = tp1_dist * config.TP2_LEVEL_MAX_MULT
    levels: list[float] = [
        ctx.prev_day_high, ctx.prev_day_low, ctx.prev_day_close,
        *ctx.key_levels_extra,
    ]
    if ctx.opening_range_locked:
        if ctx.opening_range_high is not None:
            levels.append(ctx.opening_range_high)
        if ctx.opening_range_low is not None:
            levels.append(ctx.opening_range_low)
    sign = 1.0 if direction == Direction.LONG else -1.0
    mapped = [
        lvl
        for lvl in levels
        if lvl > 0 and lo <= sign * (lvl - entry) <= hi
    ]
    if mapped:
        return min(mapped, key=lambda lvl: abs(lvl - entry))
    return entry + sign * tp1_dist * config.TP2_DIST_MULT


def _near_key_level(ctx: IndiaContext, price: float) -> str | None:
    """Name of the structural key level within LSR_KEY_LEVEL_ATR_TOL x ATR of
    *price*, else None.

    Structural levels only — PDH/PDL/PDC, the *locked* opening range (a
    forming range is not a level, IB17), and key_levels_extra (session VWAP).
    Deliberately NOT round numbers: a 0.25-ATR tolerance on NIFTY's 50-pt
    round grid would qualify a large share of arbitrary swings and gut the
    requirement.
    """
    tol = ctx.atr14_5m * config.LSR_KEY_LEVEL_ATR_TOL
    if tol <= 0:
        return None
    candidates: list[tuple[str, float]] = [
        ("prev_day_high", ctx.prev_day_high),
        ("prev_day_low", ctx.prev_day_low),
        ("prev_day_close", ctx.prev_day_close),
    ]
    if ctx.opening_range_locked:
        if ctx.opening_range_high is not None:
            candidates.append(("or_high", ctx.opening_range_high))
        if ctx.opening_range_low is not None:
            candidates.append(("or_low", ctx.opening_range_low))
    candidates.extend(("vwap", lvl) for lvl in ctx.key_levels_extra)
    for name, level in candidates:
        if level > 0 and abs(price - level) <= tol:
            return name
    return None


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
        if not _pattern_bar_ready(ctx):
            return None

        sweep = ctx.candles_5m[-1]
        # A sweep bar smaller than the trigger floor is a wick of noise, not a
        # liquidity grab — its "reclaim" carries no information.
        if (sweep.high - sweep.low) < _min_trigger_range(ctx):
            return None
        if ctx.current_volume_ratio() < config.LSR_VOLUME_MULT:
            return None

        swing_low = last_swing_low(
            ctx.candles_15m, lookback=config.LSR_SWING_LOOKBACK, width=1
        )
        swing_high = last_swing_high(
            ctx.candles_15m, lookback=config.LSR_SWING_LOOKBACK, width=1
        )

        if swing_low is not None and sweep.low < swing_low and sweep.close > swing_low:
            level = self._key_level_for(ctx, swing_low)
            if level is None:
                return None  # swept a nobody-swing — no resting-liquidity thesis
            return self._build(
                ctx, Direction.LONG, sweep, tp_level=swing_high, key_level=level
            )
        if swing_high is not None and sweep.high > swing_high and sweep.close < swing_high:
            level = self._key_level_for(ctx, swing_high)
            if level is None:
                return None
            return self._build(
                ctx, Direction.SHORT, sweep, tp_level=swing_low, key_level=level
            )
        return None

    @staticmethod
    def _key_level_for(ctx: IndiaContext, swept: float) -> str | None:
        """Which structural level the swept swing sits on — None rejects the
        sweep, "" means the requirement is disabled. Live 2026-07-10: LSR went
        0/6 sweeping arbitrary 15m swings — liquidity only rests beyond levels
        the whole market watches."""
        if not config.LSR_REQUIRE_KEY_LEVEL:
            return ""
        return _near_key_level(ctx, swept)

    def _build(
        self,
        ctx: IndiaContext,
        direction: str,
        sweep: Candle,
        tp_level: float | None,
        key_level: str = "",
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

        lot = config.lot_size_for(ctx.base)
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
            pcr_at_entry=ctx.pcr,
            tp2=_derive_tp2(ctx, entry, tp1, direction),
            setup_reason=(
                "15m swing-low sweep + reclaim"
                if direction == Direction.LONG
                else "15m swing-high sweep + reclaim"
            )
            + (f" @ {key_level}" if key_level else ""),
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
    return config.lot_size_for(base)


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
        pcr_at_entry=ctx.pcr,
        tp2=_derive_tp2(ctx, entry, tp1, direction),
        setup_reason=reason,
    )


class OpeningRangeBreakout(Evaluator):
    """§10.2 — a 5m close breaks the 09:15-09:45 opening range with volume.

    Only fires on a *locked* range (09:45+, IB17): before the lock the range
    is still forming, and "breaking" a partial range is noise — live
    2026-07-09 six ORB signals fired inside 09:15-09:16 against ~30 seconds
    of range; every one hit SL.
    """

    setup_class = SetupClass.OPENING_RANGE_BREAKOUT

    def evaluate(self, ctx: IndiaContext) -> IndiaSignal | None:
        if not self.enabled or not ctx.candles_5m or ctx.volume_avg_5m_20 <= 0:
            return None
        if not ctx.opening_range_locked:
            return None
        # The opening range stops being the relevant reference by late morning —
        # don't fire a "breakout" of a stale 09:15-09:45 range at midday.
        if (
            ctx.scan_time_ist is not None
            and ctx.scan_time_ist > config.ORB_WINDOW_END
        ):
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
        vol_ratio = ctx.current_volume_ratio()
        if vol_ratio < config.ORB_VOLUME_MULT:
            return None
        buf = ctx.atr14_5m * config.ORB_ATR_BUFFER_MULT
        # Chase guard: if price has already run past the breakout level by more
        # than MAX_CHASE_ATR, the printed entry is unfillable — skip, don't chase.
        max_chase = ctx.atr14_5m * config.MAX_CHASE_ATR
        if current.close > orh + buf:
            if current.close - (orh + buf) > max_chase:
                return None
            return self._build(ctx, Direction.LONG, orh + buf, orl - buf, vol_ratio)
        if current.close < orl - buf:
            if (orl - buf) - current.close > max_chase:
                return None
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
    vol_ratio = ctx.current_volume_ratio()
    if vol_ratio < config.VSB_VOLUME_MULT:
        return None
    if ctx.oi_change_15m_pct < config.VSB_OI_MIN_PCT:
        return None
    atr = ctx.atr14_5m
    max_chase = atr * config.MAX_CHASE_ATR
    if direction == Direction.LONG:
        level = last_swing_high(ctx.candles_15m, lookback=config.VSB_SWING_LOOKBACK, width=1)
        if level is None or not (current.high > level and current.close > level):
            return None
        entry = level + atr * config.VSB_ENTRY_ATR_MULT
        # Chase guard: price already ran past the stated entry — unfillable.
        if current.close - entry > max_chase:
            return None
        base_sl = level - atr * config.VSB_SL_ATR_MULT
        sl = min(current.open, base_sl) if current.open < level else base_sl
    else:
        level = last_swing_low(ctx.candles_15m, lookback=config.VSB_SWING_LOOKBACK, width=1)
        if level is None or not (current.low < level and current.close < level):
            return None
        entry = level - atr * config.VSB_ENTRY_ATR_MULT
        if entry - current.close > max_chase:
            return None
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


class IndiaVixExtreme(Evaluator):
    """§10.7 — fear spike (VIX high) + sharp drop + oversold bullish reclaim → long.

    Contrarian, regime-neutral by design. Only the LONG (capitulation) branch is
    implemented; the VIX-compression SHORT needs a VIX time-series in context and
    lands with that data source.
    """

    setup_class = SetupClass.INDIA_VIX_EXTREME

    def evaluate(self, ctx: IndiaContext) -> IndiaSignal | None:
        if not self.enabled or len(ctx.candles_5m) < 16:
            return None
        if ctx.india_vix <= config.VIX_EXTREME_HIGH or ctx.day_open <= 0:
            return None
        if ctx.regime_60m is Regime.TRENDING_DOWN:
            return None
        if not _pattern_bar_ready(ctx):
            return None
        current = ctx.candles_5m[-1]
        drop_pct = (ctx.day_open - current.close) / ctx.day_open * 100.0
        if drop_pct < config.VIX_EXTREME_MIN_DROP_PCT:
            return None
        if not is_bullish_rejection(
            current, ctx.candles_5m[-2], min_range=_min_trigger_range(ctx)
        ):
            return None
        if rsi([c.close for c in ctx.candles_5m], 14) >= config.VIX_EXTREME_RSI_MAX:
            return None

        entry = current.close
        sl = ctx.intraday_low - ctx.atr14_5m * config.VIX_SL_ATR_MULT
        tp1 = ctx.prev_day_close
        sl_dist = entry - sl
        if entry <= 0 or sl_dist <= 0 or tp1 <= entry:
            return None
        sl_pct = sl_dist / entry * 100.0
        if not (config.VIX_MIN_SL_PCT <= sl_pct <= config.VIX_MAX_SL_PCT):
            return None
        tp1_pct = (tp1 - entry) / entry * 100.0
        return _make_signal(
            ctx,
            self.setup_class,
            Direction.LONG,
            entry,
            sl,
            tp1,
            sl_pct,
            tp1_pct,
            tp1_pct / sl_pct,
            htf=False,
            vol_ratio=0.0,
            reason="VIX-extreme capitulation reclaim",
        )


def _nearest(value: float, candidates: list[float | None]) -> float | None:
    valid = [x for x in candidates if x is not None]
    return min(valid, key=lambda x: abs(x - value)) if valid else None


class PcrExtreme(Evaluator):
    """§10.8 — crowded option positioning; fade it at a level with a rejection.

    PCR extreme bearish (crowd holds puts) → contrarian LONG at support; PCR
    extreme bullish → contrarian SHORT at resistance. Regime-neutral.
    """

    setup_class = SetupClass.PCR_EXTREME
    index_only = True  # market-wide PCR has no per-stock equivalent

    def evaluate(self, ctx: IndiaContext) -> IndiaSignal | None:
        if not self.enabled or len(ctx.candles_5m) < 2 or len(ctx.candles_15m) < 3:
            return None
        if not _pattern_bar_ready(ctx):
            return None
        current, prev = ctx.candles_5m[-1], ctx.candles_5m[-2]
        atr = ctx.atr14_5m
        tol = atr * config.PCR_NEAR_LEVEL_ATR_MULT
        min_range = _min_trigger_range(ctx)
        swing_low = last_swing_low(ctx.candles_15m, lookback=20, width=1)
        swing_high = last_swing_high(ctx.candles_15m, lookback=20, width=1)

        if ctx.pcr_is_extreme_bearish:
            level = _nearest(
                current.close, [swing_low, ctx.prev_day_low, ctx.prev_day_close]
            )
            if level is None or abs(current.close - level) > tol:
                return None
            if ctx.oi_change_15m_pct <= 0 or not is_bullish_rejection(
                current, prev, min_range=min_range
            ):
                return None
            target = _nearest_above(current.close, [swing_high, ctx.opening_range_high])
            return self._build(ctx, Direction.LONG, current.close, level, target)

        if ctx.pcr_is_extreme_bullish:
            level = _nearest(
                current.close, [swing_high, ctx.prev_day_high, ctx.prev_day_close]
            )
            if level is None or abs(current.close - level) > tol:
                return None
            if ctx.oi_change_15m_pct <= 0 or not is_bearish_rejection(
                current, prev, min_range=min_range
            ):
                return None
            target = _nearest_below(current.close, [swing_low, ctx.opening_range_low])
            return self._build(ctx, Direction.SHORT, current.close, level, target)

        return None

    def _build(
        self,
        ctx: IndiaContext,
        direction: str,
        entry: float,
        level: float,
        target: float | None,
    ) -> IndiaSignal | None:
        if target is None or entry <= 0:
            return None
        pad = ctx.atr14_5m * config.PCR_SL_ATR_MULT
        sl = level - pad if direction == Direction.LONG else level + pad
        sl_dist = abs(entry - sl)
        if sl_dist <= 0:
            return None
        sl_pct = sl_dist / entry * 100.0
        if not (config.PCR_MIN_SL_PCT <= sl_pct <= config.PCR_MAX_SL_PCT):
            return None
        tp1_pct = abs(target - entry) / entry * 100.0
        rr = tp1_pct / sl_pct
        if rr < config.PCR_MIN_RR:
            return None
        return _make_signal(
            ctx,
            self.setup_class,
            direction,
            entry,
            sl,
            target,
            sl_pct,
            tp1_pct,
            rr,
            htf=False,
            vol_ratio=0.0,
            reason="PCR-extreme contrarian rejection",
        )


def _nearest_above(value: float, candidates: list[float | None]) -> float | None:
    above = [x for x in candidates if x is not None and x > value]
    return min(above) if above else None


def _nearest_below(value: float, candidates: list[float | None]) -> float | None:
    below = [x for x in candidates if x is not None and x < value]
    return max(below) if below else None


class TrendPullbackEma(Evaluator):
    """§10.3 — with-trend pullback to the 5m EMA21/EMA55 and reclaim.

    Only fires when the 60m regime is trending and the 60m EMA stack agrees.
    """

    setup_class = SetupClass.TREND_PULLBACK_EMA

    def evaluate(self, ctx: IndiaContext) -> IndiaSignal | None:
        if not self.enabled:
            return None
        if ctx.regime_60m not in (Regime.TRENDING_UP, Regime.TRENDING_DOWN):
            return None
        if len(ctx.candles_5m) < 56 or len(ctx.candles_60m) < 56:
            return None
        closes5 = [c.close for c in ctx.candles_5m]
        closes60 = [c.close for c in ctx.candles_60m]
        ema21_5, ema55_5 = ema(closes5, 21), ema(closes5, 55)
        ema21_60, ema55_60 = ema(closes60, 21), ema(closes60, 55)
        current = ctx.candles_5m[-1]
        atr = ctx.atr14_5m
        rsi5 = rsi(closes5, 14)
        long = ctx.regime_60m is Regime.TRENDING_UP

        if long and ema21_60 <= ema55_60:
            return None
        if not long and ema21_60 >= ema55_60:
            return None
        if not (config.TPE_RSI_MIN <= rsi5 <= config.TPE_RSI_MAX):
            return None

        near_21 = abs(current.close - ema21_5) <= abs(current.close - ema55_5)
        ema_ref = ema21_5 if near_21 else ema55_5
        if abs(current.close - ema_ref) > atr * config.TPE_PULLBACK_ATR_MULT:
            return None
        reclaim = (
            (current.low < ema_ref and current.close > ema_ref)
            if long
            else (current.high > ema_ref and current.close < ema_ref)
        )
        if not reclaim:
            return None

        direction = Direction.LONG if long else Direction.SHORT
        entry = current.close
        pad = atr * config.TPE_SL_ATR_MULT
        # Absolute-point SL floor is index-scaled; stocks use a price-relative
        # floor (8 points is 5%+ of a cheap stock — it rejected every setup).
        if ctx.base in config.INSTRUMENTS:
            min_sl_dist = config.TPE_MIN_SL_POINTS
        else:
            min_sl_dist = entry * config.TPE_MIN_SL_PCT / 100.0
        if long:
            sl = min(current.low - pad, entry - min_sl_dist)
        else:
            sl = max(current.high + pad, entry + min_sl_dist)
        sl_dist = abs(entry - sl)
        if entry <= 0 or sl_dist <= 0:
            return None
        sl_pct = sl_dist / entry * 100.0
        if not (config.TPE_MIN_SL_PCT <= sl_pct <= config.TPE_MAX_SL_PCT):
            return None

        swing = (
            last_swing_high(ctx.candles_15m, lookback=20, width=1)
            if long
            else last_swing_low(ctx.candles_15m, lookback=20, width=1)
        )
        fallback = (
            entry + sl_dist * config.TPE_TP_RR
            if long
            else entry - sl_dist * config.TPE_TP_RR
        )
        # Use the swing as target only if it clears the min R:R; otherwise the
        # 2R fallback. Without this guard a swing just beyond entry yielded
        # sub-1 R:R signals (the guard every other swing-target evaluator has).
        if (
            swing is not None
            and ((swing > entry) if long else (swing < entry))
            and abs(swing - entry) >= sl_dist * config.TPE_MIN_RR
        ):
            tp1 = swing
        else:
            tp1 = fallback
        tp1_pct = abs(tp1 - entry) / entry * 100.0
        rr = tp1_pct / sl_pct if sl_pct > 0 else 0.0
        if rr < config.TPE_MIN_RR:
            return None
        return _make_signal(
            ctx,
            self.setup_class,
            direction,
            entry,
            sl,
            tp1,
            sl_pct,
            tp1_pct,
            rr,
            htf=True,  # only fires with an aligned 60m trend
            vol_ratio=0.0,
            reason="with-trend EMA pullback reclaim",
        )


class OiSpikeReversal(Evaluator):
    """§10.13 — an OI surge at a key level with a price rejection → reversal."""

    setup_class = SetupClass.OI_SPIKE_REVERSAL

    def evaluate(self, ctx: IndiaContext) -> IndiaSignal | None:
        if not self.enabled or len(ctx.candles_5m) < 2 or len(ctx.candles_15m) < 3:
            return None
        if ctx.oi_change_15m_pct < config.OIS_OI_SPIKE_PCT:
            return None
        if ctx.current_oi < config.OIS_MIN_OI:
            return None
        if not _pattern_bar_ready(ctx):
            return None
        current, prev = ctx.candles_5m[-1], ctx.candles_5m[-2]
        atr = ctx.atr14_5m
        tol = atr * config.OIS_NEAR_LEVEL_ATR_MULT
        min_range = _min_trigger_range(ctx)
        step = config.round_step_for(ctx.base, current.close)
        base_round = round(current.close / step) * step
        swing_low = last_swing_low(ctx.candles_15m, lookback=20, width=1)
        swing_high = last_swing_high(ctx.candles_15m, lookback=20, width=1)

        support = _nearest(
            current.close,
            [swing_low, ctx.prev_day_low, ctx.prev_day_close, base_round - step, base_round],
        )
        if support is not None and abs(current.close - support) <= tol:
            if is_bullish_rejection(current, prev, min_range=min_range):
                target = _nearest_above(
                    current.close, [swing_high, ctx.opening_range_high, ctx.prev_day_high]
                )
                return self._build(ctx, Direction.LONG, current.close, support, target)

        resistance = _nearest(
            current.close,
            [swing_high, ctx.prev_day_high, ctx.prev_day_close, base_round, base_round + step],
        )
        if resistance is not None and abs(current.close - resistance) <= tol:
            if is_bearish_rejection(current, prev, min_range=min_range):
                target = _nearest_below(
                    current.close, [swing_low, ctx.opening_range_low, ctx.prev_day_low]
                )
                return self._build(ctx, Direction.SHORT, current.close, resistance, target)
        return None

    def _build(
        self,
        ctx: IndiaContext,
        direction: str,
        entry: float,
        level: float,
        target: float | None,
    ) -> IndiaSignal | None:
        if target is None or entry <= 0:
            return None
        pad = ctx.atr14_5m * config.OIS_SL_ATR_MULT
        sl = level - pad if direction == Direction.LONG else level + pad
        sl_dist = abs(entry - sl)
        if sl_dist <= 0:
            return None
        sl_pct = sl_dist / entry * 100.0
        if not (config.OIS_MIN_SL_PCT <= sl_pct <= config.OIS_MAX_SL_PCT):
            return None
        tp1_pct = abs(target - entry) / entry * 100.0
        rr = tp1_pct / sl_pct
        if rr < config.OIS_MIN_RR:
            return None
        return _make_signal(
            ctx,
            self.setup_class,
            direction,
            entry,
            sl,
            target,
            sl_pct,
            tp1_pct,
            rr,
            htf=_reversal_aligned(direction, ctx.regime_60m),
            vol_ratio=0.0,
            reason="OI-spike rejection at level",
        )


def _reversal_aligned(direction: str, regime: Regime) -> bool:
    """Reversal alignment: trend-with or ranging counts (spec §10.13)."""
    if direction == Direction.LONG:
        return regime in (Regime.TRENDING_UP, Regime.RANGING)
    return regime in (Regime.TRENDING_DOWN, Regime.RANGING)


class SrFlipRetest(Evaluator):
    """§10.6 — broken S/R flips role; retest with rejection → entry.

    Short-only by default (SR_FLIP_LONG_ENABLED=false). Uses LevelBook for
    the flip detection: a level that was support, broken cleanly, and now acts
    as resistance on the retest (or vice versa for longs).
    """

    setup_class = SetupClass.SR_FLIP_RETEST

    def evaluate(self, ctx: IndiaContext) -> IndiaSignal | None:
        if not self.enabled:
            return None
        if len(ctx.candles_5m) < 2 or len(ctx.candles_15m) < 6:
            return None
        if not _pattern_bar_ready(ctx):
            return None
        current, prev = ctx.candles_5m[-1], ctx.candles_5m[-2]
        atr5 = ctx.atr14_5m
        if atr5 <= 0:
            return None

        step = config.round_step_for(ctx.base, current.close)
        book = LevelBook.build(
            ctx.candles_15m,
            round_step=step,
            extra=[
                (ctx.prev_day_high, "prev_day_high"),
                (ctx.prev_day_low, "prev_day_low"),
                (ctx.prev_day_close, "prev_day_close"),
            ],
            swing_width=1,
            merge_tol=atr5 * 0.2,
        )

        if len(ctx.candles_15m) < 15:
            atr15 = atr5
        else:
            try:
                atr15 = compute_atr(ctx.candles_15m, 14)
            except ValueError:
                atr15 = atr5

        flip_tol = atr15 * config.SRF_FLIP_ATR_MULT
        retest_tol = atr5 * config.SRF_RETEST_ATR_MULT

        if config.SRF_SHORT_ENABLED:
            resistance = book.nearest_resistance(current.close)
            if resistance is not None:
                broke_below = any(
                    c.close < resistance.price - flip_tol
                    for c in ctx.candles_15m[-10:]
                )
                near_retest = (
                    abs(current.close - resistance.price) <= retest_tol
                )
                if broke_below and near_retest:
                    if is_bearish_rejection(
                        current, prev, min_range=_min_trigger_range(ctx)
                    ):
                        return self._build(
                            ctx, Direction.SHORT, current, resistance.price,
                            book,
                        )

        if config.SRF_LONG_ENABLED:
            support = book.nearest_support(current.close)
            if support is not None:
                broke_above = any(
                    c.close > support.price + flip_tol
                    for c in ctx.candles_15m[-10:]
                )
                near_retest = abs(current.close - support.price) <= retest_tol
                if broke_above and near_retest:
                    if is_bullish_rejection(
                        current, prev, min_range=_min_trigger_range(ctx)
                    ):
                        return self._build(
                            ctx, Direction.LONG, current, support.price, book
                        )

        return None

    def _build(
        self,
        ctx: IndiaContext,
        direction: str,
        bar: Candle,
        level: float,
        book: LevelBook,
    ) -> IndiaSignal | None:
        entry = bar.close
        pad = ctx.atr14_5m * config.SRF_SL_ATR_MULT
        sl = bar.high + pad if direction == Direction.SHORT else bar.low - pad

        sl_dist = abs(entry - sl)
        if entry <= 0 or sl_dist <= 0:
            return None
        sl_pct = sl_dist / entry * 100.0
        if not (config.SRF_MIN_SL_PCT <= sl_pct <= config.SRF_MAX_SL_PCT):
            return None

        # A flip trade needs a mapped destination: the nearest book level at
        # least SRF_MIN_RR away (the *adjacent* level is nearly always a round
        # number one step away — useless as a target). Live 2026-07-10: all 26
        # SRF emissions carried rr == exactly 1.5 because the adjacent level
        # never cleared min R:R and every signal shot the synthetic fallback,
        # netting negative at 30% of total volume. Default posture: no
        # structural level far enough to pay for the stop -> no signal.
        min_dist = sl_dist * config.SRF_MIN_RR
        if direction == Direction.SHORT:
            qualifying = [
                lv.price for lv in book.levels() if lv.price <= entry - min_dist
            ]
            book_target = max(qualifying) if qualifying else None
        else:
            qualifying = [
                lv.price for lv in book.levels() if lv.price >= entry + min_dist
            ]
            book_target = min(qualifying) if qualifying else None

        if book_target is not None:
            tp1 = book_target
        elif config.SRF_REQUIRE_BOOK_TARGET:
            return None
        else:
            tp1 = (
                entry - min_dist
                if direction == Direction.SHORT
                else entry + min_dist
            )
        tp1_pct = abs(tp1 - entry) / entry * 100.0
        rr = tp1_pct / sl_pct if sl_pct > 0 else 0.0
        return _make_signal(
            ctx,
            self.setup_class,
            direction,
            entry,
            sl,
            tp1,
            sl_pct,
            tp1_pct,
            rr,
            htf=_reversal_aligned(direction, ctx.regime_60m),
            vol_ratio=0.0,
            reason="SR-flip retest rejection",
        )


class FailedAuctionReclaim(Evaluator):
    """§10.9 — false breakout above OR_HIGH / below OR_LOW then reclaim.

    The "trap and reverse": weak hands flushed on the false break, strong
    hands enter on the reclaim bar with volume.
    """

    setup_class = SetupClass.FAILED_AUCTION_RECLAIM

    def evaluate(self, ctx: IndiaContext) -> IndiaSignal | None:
        if not self.enabled:
            return None
        n = len(ctx.candles_5m)
        if n < config.FAR_SL_LOOKBACK + 1 or ctx.volume_avg_5m_20 <= 0:
            return None
        # OR levels only count once the range is locked (09:45, IB17) — a
        # "failed auction" of a still-forming range is not a level trade.
        # PDH/PDL are final from the previous session and always valid.
        orh = ctx.opening_range_high if ctx.opening_range_locked else None
        orl = ctx.opening_range_low if ctx.opening_range_locked else None
        current = ctx.candles_5m[-1]
        lookback = ctx.candles_5m[-config.FAR_SL_LOOKBACK - 1 : -1]
        vol_ratio = ctx.current_volume_ratio()

        if vol_ratio < config.FAR_VOLUME_MULT:
            return None

        levels_above = [
            lv
            for lv in [orh, ctx.prev_day_high]
            if lv is not None and lv > 0
        ]
        levels_below = [
            lv
            for lv in [orl, ctx.prev_day_low]
            if lv is not None and lv > 0
        ]

        for level in levels_above:
            false_break = any(c.high > level and c.close < level for c in lookback)
            reclaim = current.close > level
            if false_break and reclaim:
                sl = min(c.low for c in lookback)
                return self._build(
                    ctx, Direction.LONG, current.close, sl, level
                )

        for level in levels_below:
            false_break = any(c.low < level and c.close > level for c in lookback)
            reclaim = current.close < level
            if false_break and reclaim:
                sl = max(c.high for c in lookback)
                return self._build(
                    ctx, Direction.SHORT, current.close, sl, level
                )

        return None

    def _build(
        self,
        ctx: IndiaContext,
        direction: str,
        entry: float,
        sl: float,
        level: float,
    ) -> IndiaSignal | None:
        sl_dist = abs(entry - sl)
        if entry <= 0 or sl_dist <= 0:
            return None
        sl_pct = sl_dist / entry * 100.0
        if not (config.FAR_MIN_SL_PCT <= sl_pct <= config.FAR_MAX_SL_PCT):
            return None

        swing = (
            last_swing_high(ctx.candles_15m, lookback=20, width=1)
            if direction == Direction.LONG
            else last_swing_low(ctx.candles_15m, lookback=20, width=1)
        ) if len(ctx.candles_15m) >= 3 else None
        fallback = (
            entry + sl_dist * 2.0
            if direction == Direction.LONG
            else entry - sl_dist * 2.0
        )
        if direction == Direction.LONG:
            tp1 = swing if (swing is not None and swing > entry) else fallback
        else:
            tp1 = swing if (swing is not None and swing < entry) else fallback
        tp1_pct = abs(tp1 - entry) / entry * 100.0
        rr = tp1_pct / sl_pct if sl_pct > 0 else 0.0
        if rr < config.FAR_MIN_RR:
            return None
        return _make_signal(
            ctx,
            self.setup_class,
            direction,
            entry,
            sl,
            tp1,
            sl_pct,
            tp1_pct,
            rr,
            htf=_trend_matches(direction, ctx.regime_60m),
            vol_ratio=0.0,
            reason="failed-auction reclaim",
        )


class DivergenceContinuation(Evaluator):
    """§10.10 — RSI diverges from price → momentum exhaustion signal.

    Bearish divergence (price new high, RSI lower) → SHORT; bullish
    divergence (price new low, RSI higher) → LONG.

    Tightened (Session 15) after live data showed this evaluator producing
    48% of all emissions at a 15.6% win rate: any new extreme with a
    marginally weaker RSI qualified — a condition that stays true bar after
    bar in a steady drift and mass-fired against market-wide moves. A real
    exhaustion divergence requires the *prior* extreme to have printed at a
    stretched RSI (>= DIV_RSI_EXTREME for shorts, mirrored for longs), a
    material RSI fade (>= DIV_MIN_RSI_MARGIN), and an actual rejection
    candle at the new extreme — not merely a red/green close.
    """

    setup_class = SetupClass.DIVERGENCE_CONTINUATION

    def evaluate(self, ctx: IndiaContext) -> IndiaSignal | None:
        if not self.enabled:
            return None
        if not _pattern_bar_ready(ctx):
            return None
        lb = config.DIV_LOOKBACK
        need = lb + 15
        if len(ctx.candles_5m) < need:
            return None
        closes = [c.close for c in ctx.candles_5m]
        current = ctx.candles_5m[-1]
        prev = ctx.candles_5m[-2]
        recent = ctx.candles_5m[-lb:]
        prior_window = ctx.candles_5m[-(lb + lb) : -lb] if len(ctx.candles_5m) >= lb * 2 else []

        if not prior_window:
            return None

        rsi_now = rsi(closes, 14)
        rsi_extreme_high = config.DIV_RSI_EXTREME
        rsi_extreme_low = 100.0 - config.DIV_RSI_EXTREME
        margin = config.DIV_MIN_RSI_MARGIN

        prior_high_val = max(c.high for c in prior_window)
        current_high_val = max(c.high for c in recent)
        prior_high_idx = next(
            i for i, c in enumerate(ctx.candles_5m)
            if c.high == prior_high_val
            and len(ctx.candles_5m) - lb * 2 <= i < len(ctx.candles_5m) - lb
        )
        rsi_at_prior_high = rsi(closes[: prior_high_idx + 1], 14) if prior_high_idx >= 15 else None

        if (
            current_high_val > prior_high_val
            and rsi_at_prior_high is not None
            and rsi_at_prior_high >= rsi_extreme_high
            and rsi_now <= rsi_at_prior_high - margin
            and rsi_now > 40
            and is_bearish_rejection(current, prev, min_range=_min_trigger_range(ctx))
        ):
            div_high = current_high_val
            return self._build(ctx, Direction.SHORT, current.close, div_high)

        prior_low_val = min(c.low for c in prior_window)
        current_low_val = min(c.low for c in recent)
        prior_low_idx = next(
            i for i, c in enumerate(ctx.candles_5m)
            if c.low == prior_low_val
            and len(ctx.candles_5m) - lb * 2 <= i < len(ctx.candles_5m) - lb
        )
        rsi_at_prior_low = rsi(closes[: prior_low_idx + 1], 14) if prior_low_idx >= 15 else None

        if (
            current_low_val < prior_low_val
            and rsi_at_prior_low is not None
            and rsi_at_prior_low <= rsi_extreme_low
            and rsi_now >= rsi_at_prior_low + margin
            and rsi_now < 60
            and is_bullish_rejection(current, prev, min_range=_min_trigger_range(ctx))
        ):
            div_low = current_low_val
            return self._build(ctx, Direction.LONG, current.close, div_low)

        return None

    def _build(
        self,
        ctx: IndiaContext,
        direction: str,
        entry: float,
        divergence_extreme: float,
    ) -> IndiaSignal | None:
        pad = ctx.atr14_5m * config.DIV_SL_ATR_MULT
        if direction == Direction.SHORT:
            sl = divergence_extreme + pad
        else:
            sl = divergence_extreme - pad
        sl_dist = abs(entry - sl)
        if entry <= 0 or sl_dist <= 0:
            return None
        sl_pct = sl_dist / entry * 100.0
        if not (config.DIV_MIN_SL_PCT <= sl_pct <= config.DIV_MAX_SL_PCT):
            return None

        if direction == Direction.SHORT:
            targets: list[float | None] = [
                ctx.prev_day_low, ctx.opening_range_low,
            ]
            target = _nearest_below(entry, targets)
        else:
            targets = [ctx.prev_day_high, ctx.opening_range_high]
            target = _nearest_above(entry, targets)
        if target is None:
            target = (
                entry - sl_dist * 2.0
                if direction == Direction.SHORT
                else entry + sl_dist * 2.0
            )
        tp1_pct = abs(target - entry) / entry * 100.0
        rr = tp1_pct / sl_pct if sl_pct > 0 else 0.0
        if rr < config.DIV_MIN_RR:
            return None

        htf = (
            ctx.regime_60m is not Regime.TRENDING_DOWN
            if direction == Direction.LONG
            else ctx.regime_60m is not Regime.TRENDING_UP
        )
        return _make_signal(
            ctx,
            self.setup_class,
            direction,
            entry,
            sl,
            target,
            sl_pct,
            tp1_pct,
            rr,
            htf=htf,
            vol_ratio=0.0,
            reason="RSI-price divergence",
        )


class QuietCompressionBreak(Evaluator):
    """§10.11 — Bollinger squeeze breakout during mid-session (10:00–14:00 IST).

    Requires a minimum squeeze duration before the breakout bar, volume
    confirmation, and a tight stop at the Bollinger midline.
    """

    setup_class = SetupClass.QUIET_COMPRESSION_BREAK

    def evaluate(self, ctx: IndiaContext) -> IndiaSignal | None:
        if not self.enabled:
            return None
        from datetime import time as _time

        if ctx.scan_time_ist is not None:
            if not (_time(10, 0) <= ctx.scan_time_ist <= _time(14, 0)):
                return None

        n = len(ctx.candles_5m)
        need = 20 + config.QCB_MIN_SQUEEZE_BARS
        if n < need or ctx.volume_avg_5m_20 <= 0:
            return None

        closes = [c.close for c in ctx.candles_5m]
        current = ctx.candles_5m[-1]
        vol_ratio = ctx.current_volume_ratio()
        if vol_ratio < config.QCB_VOLUME_MULT:
            return None

        bb_upper, bb_mid, bb_lower = bollinger(closes, 20)
        if bb_mid <= 0:
            return None

        squeeze_count = 0
        for offset in range(1, config.QCB_MIN_SQUEEZE_BARS + 1):
            prior_closes = closes[: n - offset]
            if len(prior_closes) < 20:
                break
            u, m, lo = bollinger(prior_closes, 20)
            if m > 0 and (u - lo) / m < config.QCB_BB_SQUEEZE_THRESHOLD:
                squeeze_count += 1

        if squeeze_count < config.QCB_MIN_SQUEEZE_BARS:
            return None

        atr5 = ctx.atr14_5m
        sl_pad = atr5 * config.QCB_SL_ATR_MULT

        if current.close > bb_upper:
            direction = Direction.LONG
            entry = current.close
            sl = bb_mid - sl_pad
        elif current.close < bb_lower:
            direction = Direction.SHORT
            entry = current.close
            sl = bb_mid + sl_pad
        else:
            return None

        sl_dist = abs(entry - sl)
        if entry <= 0 or sl_dist <= 0:
            return None
        sl_pct = sl_dist / entry * 100.0
        if not (config.QCB_MIN_SL_PCT <= sl_pct <= config.QCB_MAX_SL_PCT):
            return None

        swing = (
            last_swing_high(ctx.candles_15m, lookback=20, width=1)
            if direction == Direction.LONG
            else last_swing_low(ctx.candles_15m, lookback=20, width=1)
        ) if len(ctx.candles_15m) >= 3 else None
        fallback = (
            entry + sl_dist * config.QCB_MIN_RR
            if direction == Direction.LONG
            else entry - sl_dist * config.QCB_MIN_RR
        )
        if direction == Direction.LONG:
            or_target = ctx.opening_range_high
            tp1 = swing if (swing is not None and swing > entry) else fallback
            if or_target is not None and or_target > entry:
                tp1 = min(tp1, or_target)
        else:
            or_target = ctx.opening_range_low
            tp1 = swing if (swing is not None and swing < entry) else fallback
            if or_target is not None and or_target < entry:
                tp1 = max(tp1, or_target)

        if direction == Direction.LONG:
            min_tp = entry + sl_dist * config.QCB_MIN_RR
        else:
            min_tp = entry - sl_dist * config.QCB_MIN_RR
        if direction == Direction.LONG and tp1 < min_tp:
            tp1 = min_tp
        if direction == Direction.SHORT and tp1 > min_tp:
            tp1 = min_tp

        tp1_pct = abs(tp1 - entry) / entry * 100.0
        rr = tp1_pct / sl_pct if sl_pct > 0 else 0.0
        return _make_signal(
            ctx,
            self.setup_class,
            direction,
            entry,
            sl,
            tp1,
            sl_pct,
            tp1_pct,
            rr,
            htf=_trend_matches(direction, ctx.regime_60m),
            vol_ratio=vol_ratio,
            reason="Bollinger squeeze breakout",
        )


class MaCrossTrendShift(Evaluator):
    """§10.12 — EMA21/EMA55 crossover on 15m signals a trend shift.

    HTF filter required: cross must not oppose the 60m regime. Cooldown
    (once per session) is enforced at the scanner level, not here.
    """

    setup_class = SetupClass.MA_CROSS_TREND_SHIFT

    def evaluate(self, ctx: IndiaContext) -> IndiaSignal | None:
        if not self.enabled:
            return None
        bars15 = self._completed_bars(ctx)
        if len(bars15) < 57:
            return None
        closes15 = [c.close for c in bars15]
        ema21_series = ema_series(closes15, 21)
        ema55_series = ema_series(closes15, 55)
        ema21_cur = ema21_series[-1]
        ema55_cur = ema55_series[-1]
        ema21_prev = ema21_series[-2]
        ema55_prev = ema55_series[-2]

        if ema21_prev < ema55_prev and ema21_cur > ema55_cur:
            direction = Direction.LONG
        elif ema21_prev > ema55_prev and ema21_cur < ema55_cur:
            direction = Direction.SHORT
        else:
            return None

        if direction == Direction.LONG and ctx.regime_60m is Regime.TRENDING_DOWN:
            return None
        if direction == Direction.SHORT and ctx.regime_60m is Regime.TRENDING_UP:
            return None

        cross_bar = bars15[-1]
        if ctx.volume_avg_15m_20 > 0:
            if cross_bar.volume / ctx.volume_avg_15m_20 < config.MAC_VOLUME_MULT:
                return None

        entry = cross_bar.close
        if len(bars15) >= 15:
            try:
                atr15 = compute_atr(bars15, 14)
            except ValueError:
                atr15 = ctx.atr14_5m
        else:
            atr15 = ctx.atr14_5m

        pad = atr15 * config.MAC_SL_ATR_MULT
        sl = ema55_cur - pad if direction == Direction.LONG else ema55_cur + pad
        sl_dist = abs(entry - sl)
        if entry <= 0 or sl_dist <= 0:
            return None
        sl_pct = sl_dist / entry * 100.0
        if not (config.MAC_MIN_SL_PCT <= sl_pct <= config.MAC_MAX_SL_PCT):
            return None

        swing = (
            last_swing_high(ctx.candles_15m, lookback=20, width=1)
            if direction == Direction.LONG
            else last_swing_low(ctx.candles_15m, lookback=20, width=1)
        )
        fallback = (
            entry + sl_dist * 2.0
            if direction == Direction.LONG
            else entry - sl_dist * 2.0
        )
        if direction == Direction.LONG:
            tp_candidates = [s for s in [swing, ctx.prev_day_high] if s is not None and s > entry]
            tp1 = min(tp_candidates) if tp_candidates else fallback
        else:
            tp_candidates = [s for s in [swing, ctx.prev_day_low] if s is not None and s < entry]
            tp1 = max(tp_candidates) if tp_candidates else fallback
        tp1_pct = abs(tp1 - entry) / entry * 100.0
        rr = tp1_pct / sl_pct if sl_pct > 0 else 0.0
        if rr < config.MAC_MIN_RR:
            return None

        htf = (
            ctx.regime_60m is not Regime.TRENDING_DOWN
            if direction == Direction.LONG
            else ctx.regime_60m is not Regime.TRENDING_UP
        )
        return _make_signal(
            ctx,
            self.setup_class,
            direction,
            entry,
            sl,
            tp1,
            sl_pct,
            tp1_pct,
            rr,
            htf=htf,
            vol_ratio=0.0,
            reason="EMA21/55 cross on 15m",
        )

    @staticmethod
    def _completed_bars(ctx: IndiaContext) -> list[Candle]:
        """15m bars with the still-forming bar dropped.

        A cross detected on the building bar can flicker in and out with
        every tick and print entry/SL off a half-formed close; a completed
        bar's cross is decided once.
        """
        bars = ctx.candles_15m
        if not bars or ctx.scan_time_ist is None:
            return list(bars)
        last_start = bars[-1].ts
        start_min = last_start.hour * 60 + last_start.minute
        now_min = ctx.scan_time_ist.hour * 60 + ctx.scan_time_ist.minute
        if 0 <= now_min - start_min < 15:
            return list(bars[:-1])
        return list(bars)


class ExpiryGammaSqueeze(Evaluator):
    """§10.14 — expiry-day gamma squeeze toward max pain (13:30–15:00 IST).

    Only active when ``is_expiry_day`` is True and scan time is in the
    IB16 window (after 13:30 IST — the last ~2 hours of expiry trading).
    Fires at most once per instrument per session (cooldown enforced at
    scanner level).
    """

    setup_class = SetupClass.EXPIRY_GAMMA_SQUEEZE
    enabled = config.EGS_ENABLED
    index_only = True  # keys off index max-pain / weekly options expiry

    def evaluate(self, ctx: IndiaContext) -> IndiaSignal | None:
        if not self.enabled:
            return None
        if not ctx.is_expiry_day:
            return None
        from datetime import time as _time

        if ctx.scan_time_ist is not None:
            if not (_time(13, 30) <= ctx.scan_time_ist <= _time(15, 0)):
                return None

        if ctx.max_pain_strike is None or not ctx.candles_5m:
            return None
        last_price = ctx.candles_5m[-1].close
        if last_price <= 0:
            return None

        gap_pct = abs(last_price - ctx.max_pain_strike) / last_price * 100.0
        if not (config.EGS_MIN_DISTANCE_PCT <= gap_pct <= config.EGS_MAX_DISTANCE_PCT):
            return None

        if ctx.max_pain_strike > last_price:
            direction = Direction.LONG
        else:
            direction = Direction.SHORT

        entry = last_price
        atr5 = ctx.atr14_5m
        pad = atr5 * config.EGS_SL_ATR_MULT
        sl = entry - pad if direction == Direction.LONG else entry + pad
        sl_dist = abs(entry - sl)
        if sl_dist <= 0:
            return None
        sl_pct = sl_dist / entry * 100.0
        if not (config.EGS_MIN_SL_PCT <= sl_pct <= config.EGS_MAX_SL_PCT):
            return None

        tp1 = ctx.max_pain_strike
        tp1_pct = abs(tp1 - entry) / entry * 100.0
        rr = tp1_pct / sl_pct if sl_pct > 0 else 0.0
        return _make_signal(
            ctx,
            self.setup_class,
            direction,
            entry,
            sl,
            tp1,
            sl_pct,
            tp1_pct,
            rr,
            htf=False,
            vol_ratio=0.0,
            reason="expiry-day gamma squeeze toward max pain",
        )
