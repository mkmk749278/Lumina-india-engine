"""The market-context vector — "what regime is the whole tape in *now*".

Every signal-quality decision in `INDIA_MARKET_DOCTRINE.md` is made against
market structure, not indicators in isolation: the intraday session phase, the
India VIX volatility regime, market-wide option positioning (PCR), and the
prevailing market direction (index bias + NIFTY/BANKNIFTY leadership). The
scanner computes all of these per-instrument already but discards the
*market-wide* read; this module folds the index contexts of one scan into a
single `MarketContext` that is stamped onto every emitted signal.

Stamping it makes the read first-class in the record: `/api/signals`, the ops
Strategy view, and the signals CSV gain `market_direction`, `session_phase`,
and `vix_regime` columns — the exact slices that on 2026-07-13 had to be
computed by hand to see that SHORT signals went 13% while LONG went 56% in a
long-biased tape. It is the backbone the Strategy×Context edge matrix and the
allocator read next. No new I/O: every input is already in the per-scan
contexts (cost-safe, IB18).

This layer only *measures* — it does not gate or score. Phase-aware and
direction-aware suppression are separate, owner-sign-off changes that consume
this same vector later.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time

import config
from src import dependency
from src.regime import Regime
from src.signals.model import Direction, IndiaContext


class SessionPhase:
    """Intraday phase of the NSE session (INDIA_MARKET_DOCTRINE §3)."""

    PREOPEN = "PREOPEN"
    POWER_HOUR = "POWER_HOUR"
    MIDDAY_CHOP = "MIDDAY_CHOP"
    CLOSING = "CLOSING"
    CLOSED = "CLOSED"


class VixRegime:
    """India VIX volatility regime."""

    UNKNOWN = "UNKNOWN"
    LOW = "LOW"
    NORMAL = "NORMAL"
    ELEVATED = "ELEVATED"
    EXTREME = "EXTREME"


class MarketDirection:
    """Prevailing market-wide directional bias."""

    LONG_BIASED = "LONG_BIASED"
    SHORT_BIASED = "SHORT_BIASED"
    NEUTRAL = "NEUTRAL"


# VIX event/extreme threshold — same env var the scanner's event_risk_gate
# reads, so the "stand down" line and the regime label never disagree.
_VIX_EVENT_THRESHOLD: float = config._safe_float("INDIA_VIX_EVENT_THRESHOLD", 25.0)

# The indices that vote on market direction, primary first.
_DIRECTION_INDICES = ("NIFTY", "BANKNIFTY")


def classify_session_phase(t: time | None) -> str:
    """Session phase for an IST wall-clock time."""
    if t is None:
        return SessionPhase.CLOSED
    if t < config.MARKET_OPEN:
        return SessionPhase.PREOPEN
    if t < config.POWER_HOUR_END:
        return SessionPhase.POWER_HOUR
    if t < config.MIDDAY_END:
        return SessionPhase.MIDDAY_CHOP
    if t <= config.MARKET_CLOSE:
        return SessionPhase.CLOSING
    return SessionPhase.CLOSED


def classify_vix_regime(vix: float) -> str:
    """VIX regime. 0.0 = unavailable (stale/never polled, same freshness
    doctrine as the context's vix) → UNKNOWN, never a real 'low'."""
    if vix <= 0:
        return VixRegime.UNKNOWN
    if vix < config.VIX_LOW_THRESHOLD:
        return VixRegime.LOW
    if vix < config.VIX_EXTREME_HIGH:
        return VixRegime.NORMAL
    if vix < _VIX_EVENT_THRESHOLD:
        return VixRegime.ELEVATED
    return VixRegime.EXTREME


def _daily_regime_vote(regime: Regime) -> str:
    if regime == Regime.TRENDING_UP:
        return Direction.LONG
    if regime == Regime.TRENDING_DOWN:
        return Direction.SHORT
    return dependency.NEUTRAL


def _fii_dii_vote(net_cr: float) -> str:
    """Prev-day combined FII+DII net cash → directional vote. 0.0 (unavailable)
    or sub-threshold flow → NEUTRAL (never a fabricated bias)."""
    if net_cr >= config.FII_DII_MIN_CR:
        return Direction.LONG
    if net_cr <= -config.FII_DII_MIN_CR:
        return Direction.SHORT
    return dependency.NEUTRAL


def _open_gap_pct(ctx: IndiaContext) -> float:
    """Today's opening gap (%) vs prev close — the realised overnight-sentiment
    signal that stands in for Gift-Nifty (unavailable via the Fyers feed)."""
    if ctx.prev_day_close > 0 and ctx.day_open > 0:
        return (ctx.day_open - ctx.prev_day_close) / ctx.prev_day_close * 100.0
    return 0.0


def _open_gap_vote(gap_pct: float) -> str:
    if gap_pct >= config.OPEN_GAP_MIN_PCT:
        return Direction.LONG
    if gap_pct <= -config.OPEN_GAP_MIN_PCT:
        return Direction.SHORT
    return dependency.NEUTRAL


def classify_market_direction(index_contexts: dict[str, IndiaContext]) -> str:
    """Composite market direction from the index contexts.

    Votes: the intraday bias of NIFTY and BANKNIFTY (``dependency.market_bias``)
    plus NIFTY's daily-timeframe regime. Deliberately conservative — a
    directional label needs at least two aligned votes and *zero* opposing
    ones, so a mixed/flat tape stays NEUTRAL and the eventual direction gate
    only ever haircuts clearly counter-trend signals (the 2026-07-13 short
    bleed fired into exactly this kind of aligned-up tape)."""
    votes: list[str] = []
    for name in _DIRECTION_INDICES:
        ctx = index_contexts.get(name)
        if ctx is not None:
            votes.append(dependency.market_bias(ctx))
    primary = index_contexts.get("NIFTY") or next(
        iter(index_contexts.values()), None
    )
    if primary is not None:
        votes.append(_daily_regime_vote(primary.regime_daily))
        # Macro votes (market-wide): prev-day FII/DII flow + the opening gap.
        votes.append(_fii_dii_vote(primary.fii_dii_net_cr))
        votes.append(_open_gap_vote(_open_gap_pct(primary)))

    longs = votes.count(Direction.LONG)
    shorts = votes.count(Direction.SHORT)
    if longs >= 2 and shorts == 0:
        return MarketDirection.LONG_BIASED
    if shorts >= 2 and longs == 0:
        return MarketDirection.SHORT_BIASED
    return MarketDirection.NEUTRAL


def _leadership(index_contexts: dict[str, IndiaContext]) -> str:
    """Whichever of NIFTY / BANKNIFTY has moved more from its open leads;
    NEUTRAL when neither has a real day_open or the moves are tied."""
    best = dependency.NEUTRAL
    best_move = 0.0
    for name in _DIRECTION_INDICES:
        ctx = index_contexts.get(name)
        if ctx is None or ctx.day_open <= 0 or not ctx.candles_5m:
            continue
        move = abs(ctx.candles_5m[-1].close - ctx.day_open) / ctx.day_open
        if move > best_move:
            best_move, best = move, name
    return best


@dataclass(frozen=True)
class MarketContext:
    """Market-wide read for one scan. Stamped onto every emitted signal."""

    session_phase: str
    vix: float
    vix_regime: str
    pcr: float
    market_direction: str
    leader: str
    is_expiry_day: bool
    fii_dii_net_cr: float
    open_gap_pct: float

    @classmethod
    def build(
        cls, index_contexts: dict[str, IndiaContext], now: datetime
    ) -> MarketContext:
        """Fold this scan's index contexts into the market-wide vector.

        ``index_contexts`` is the subset of the scan's contexts whose base is
        an index (NIFTY/BANKNIFTY/FINNIFTY/NIFTYNXT50). ``now`` is IST-aware or
        naive-IST; only its wall-clock time is used for the session phase.
        """
        ist = now.astimezone(config.IST) if now.tzinfo else now
        primary = index_contexts.get("NIFTY") or next(
            iter(index_contexts.values()), None
        )
        vix = primary.india_vix if primary is not None else 0.0
        pcr = primary.pcr if primary is not None else 0.0
        is_expiry = primary.is_expiry_day if primary is not None else False
        fii_dii = primary.fii_dii_net_cr if primary is not None else 0.0
        gap = _open_gap_pct(primary) if primary is not None else 0.0
        return cls(
            session_phase=classify_session_phase(ist.timetz()),
            vix=vix,
            vix_regime=classify_vix_regime(vix),
            pcr=pcr,
            market_direction=classify_market_direction(index_contexts),
            leader=_leadership(index_contexts),
            is_expiry_day=is_expiry,
            fii_dii_net_cr=fii_dii,
            open_gap_pct=round(gap, 3),
        )
