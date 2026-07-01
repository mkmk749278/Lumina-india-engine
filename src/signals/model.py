"""The signal + scan-context contract.

``IndiaSignal`` is the record every evaluator produces and the scorer, router,
FCM dispatcher, and SQLite store consume. ``IndiaContext`` is the read-only
snapshot an evaluator/scorer sees for one instrument on one scan.

The 15-value coupling note from the crypto engine applies: ``SetupClass`` names
are stringly-coupled to scoring affinity keys and telemetry event names — rename
in all places at once.

Scope note (CLAUDE.md — no scaffolds): ``IndiaContext`` currently carries only
the fields the scorer consumes. It grows field-by-field as each evaluator that
reads a new field lands — we do not pre-declare context fields nothing reads.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from src.market.candle import Candle
from src.regime import Regime


class Direction:
    LONG = "LONG"
    SHORT = "SHORT"


class SetupClass:
    """Evaluator identity. Values are coupled to scoring + telemetry keys."""

    LIQUIDITY_SWEEP_REVERSAL = "LIQUIDITY_SWEEP_REVERSAL"
    OPENING_RANGE_BREAKOUT = "OPENING_RANGE_BREAKOUT"
    TREND_PULLBACK_EMA = "TREND_PULLBACK_EMA"
    VOLUME_SURGE_BREAKOUT = "VOLUME_SURGE_BREAKOUT"
    BREAKDOWN_SHORT = "BREAKDOWN_SHORT"
    SR_FLIP_RETEST = "SR_FLIP_RETEST"
    INDIA_VIX_EXTREME = "INDIA_VIX_EXTREME"
    PCR_EXTREME = "PCR_EXTREME"
    FAILED_AUCTION_RECLAIM = "FAILED_AUCTION_RECLAIM"
    DIVERGENCE_CONTINUATION = "DIVERGENCE_CONTINUATION"
    QUIET_COMPRESSION_BREAK = "QUIET_COMPRESSION_BREAK"
    MA_CROSS_TREND_SHIFT = "MA_CROSS_TREND_SHIFT"
    OI_SPIKE_REVERSAL = "OI_SPIKE_REVERSAL"
    EXPIRY_GAMMA_SQUEEZE = "EXPIRY_GAMMA_SQUEEZE"


class Tier:
    A_PLUS = "A+"
    B = "B"
    FILTERED = "FILTERED"


@dataclass
class IndiaSignal:
    """A candidate/emitted signal. ``confidence``/``tier`` are filled post-scoring."""

    signal_id: str
    symbol: str
    base: str
    direction: str
    setup_class: str
    entry: float
    sl: float
    tp1: float
    sl_pct: float
    tp1_pct: float
    rr_ratio: float
    lot_size: int

    # Scoring inputs the evaluator sets.
    htf_trend_aligned: bool = False
    breakout_volume_ratio: float = 0.0
    setup_reason: str = ""

    # Snapshot context (stamped at emit for the record).
    regime_60m: Regime = Regime.RANGING
    regime_daily: Regime = Regime.RANGING
    atr_at_entry: float = 0.0
    vix_at_entry: float = 0.0
    pcr_at_entry: float = 0.0
    expiry_date: date | None = None
    days_to_expiry: int = 0
    dispatch_timestamp: float = 0.0

    # Filled after scoring / routing.
    tp2: float = 0.0
    confidence: float = 0.0
    tier: str = Tier.FILTERED
    suppression_reason: str = ""


@dataclass
class IndiaContext:
    """Read-only per-instrument snapshot the scorer/evaluators read for one scan."""

    base: str
    regime_60m: Regime
    regime_daily: Regime
    candles_5m: list[Candle]
    volume_avg_5m_20: float
    atr14_5m: float
    prev_day_high: float
    prev_day_low: float
    prev_day_close: float
    oi_change_15m_pct: float
    india_vix: float
    pcr_is_extreme_bearish: bool = False
    pcr_is_extreme_bullish: bool = False
    opening_range_high: float | None = None
    opening_range_low: float | None = None
    key_levels_extra: list[float] = field(default_factory=list)
    # Instrument identity + higher-timeframe candles the evaluators read.
    symbol: str = ""
    tick_size: float = 0.05
    candles_15m: list[Candle] = field(default_factory=list)
