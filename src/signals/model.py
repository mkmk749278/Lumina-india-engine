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
from datetime import date, time

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
    A = "A"
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
    # True once the 09:15-09:45 opening range is final (IB17). Evaluators that
    # trade the range (ORB, FAR's OR legs) must not treat a still-forming
    # partial range as a level — "breaking" 30 seconds of range is noise.
    opening_range_locked: bool = False
    key_levels_extra: list[float] = field(default_factory=list)
    # Instrument identity + higher-timeframe candles the evaluators read.
    symbol: str = ""
    tick_size: float = 0.05
    candles_15m: list[Candle] = field(default_factory=list)
    # Intraday reference points (session open + running extremes).
    day_open: float = 0.0
    intraday_high: float = 0.0
    intraday_low: float = 0.0
    # 60m candles (trend-pullback EMAs) + absolute OI (OI-spike gate).
    candles_60m: list[Candle] = field(default_factory=list)
    current_oi: float = 0.0
    # Scan timestamp (IST) for time-windowed evaluators (QCB, EGS).
    scan_time_ist: time | None = None
    # Expiry-day flag + max-pain strike (EXPIRY_GAMMA_SQUEEZE).
    is_expiry_day: bool = False
    max_pain_strike: float | None = None
    # 15m volume average for MA_CROSS volume gate.
    volume_avg_15m_20: float = 0.0
    # Time-of-day normalised + building-bar pro-rated volume ratio for the
    # newest 5m bar (src/market_profile.py). 0.0 = unavailable; consumers use
    # ``current_volume_ratio()`` which falls back to the raw ratio.
    volume_ratio_tod: float = 0.0
    # Intraday bias of this base's proxy index (src/dependency.py), stamped by
    # the scanner after all contexts are built: "LONG" | "SHORT" | "NEUTRAL".
    index_bias: str = "NEUTRAL"
    # Elapsed fraction (0..1) of the forming 5m bar at scan time. 1.0 = the
    # newest bar is complete (or effectively so). Pattern-triggered evaluators
    # (sweep/reclaim/rejection) only judge a bar that is at least
    # PATTERN_BAR_MIN_ELAPSED formed — a pin bar seen 40s into a bar routinely
    # un-forms by the close (live 2026-07-10: LSR 1/9, all losses forming-bar
    # reclaims that evaporated). Defaults to 1.0 so directly built contexts
    # exercise setup logic; the builder stamps the real value.
    bar_elapsed_fraction: float = 1.0
    # Age (seconds) of the newest *live tick* for this symbol at scan time.
    # None = no live tick has ever reached the store — the candles are pure
    # historical seed. The stale_data_gate suppresses on None or age above
    # INDIA_MAX_TICK_AGE_SEC: a signal computed off frozen data has an entry
    # nobody can fill (live 2026-07-10: dead WebSocket, identical duplicate
    # signals, live P&L pinned at +0.00%). Defaults to 0.0 (fresh) so directly
    # constructed contexts exercise the setup logic; the builder always stamps
    # the real value.
    last_tick_age_sec: float | None = 0.0

    def current_volume_ratio(self) -> float:
        """Newest-5m-bar volume ratio: TOD-normalised when available, else the
        raw last-bar ÷ 20-bar-average ratio, else 0.0."""
        if self.volume_ratio_tod > 0:
            return self.volume_ratio_tod
        if self.candles_5m and self.volume_avg_5m_20 > 0:
            return self.candles_5m[-1].volume / self.volume_avg_5m_20
        return 0.0
