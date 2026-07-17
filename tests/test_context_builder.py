"""IndiaContextBuilder — assembles IndiaContext from all stores."""

from __future__ import annotations

from datetime import datetime, timedelta

import config
from src.data.india_context_builder import IndiaContextBuilder
from src.data.india_market_data import IndiaMarketData
from src.data.india_oi_store import IndiaOIStore
from src.data.india_tick_store import IndiaTickStore
from src.market.candle import Candle
from src.session.expiry_manager import ExpiryManager

IST = config.IST
_SYM = "NSE:NIFTY26JULFUT"
_BASE = "NIFTY"


_BASE_DT = IST.localize(datetime(2026, 7, 7, 0, 0, 0))


def _ist(h: int, m: int) -> datetime:
    return _BASE_DT + timedelta(hours=h, minutes=m)


def _make_stores():
    tick = IndiaTickStore()
    oi = IndiaOIStore()
    mkt = IndiaMarketData()
    expiry = ExpiryManager()

    candles = [
        Candle(ts=_ist(9, 15 + i * 5), open=24000.0 + i * 10,
               high=24010.0 + i * 10, low=23990.0 + i * 10,
               close=24005.0 + i * 10, volume=1000.0 + i * 50)
        for i in range(60)
    ]
    tick.seed(_SYM, candles)

    # OI must be recent — snapshots older than INDIA_OI_TTL_SEC read as
    # unavailable (a dead poller must not look like its last observation).
    oi.update_oi(_SYM, 5_000_000.0, datetime.now(IST))
    oi.update_pcr(total_put_oi=900_000.0, total_call_oi=1_000_000.0)

    mkt.update_vix(16.5)
    mkt.update_max_pain(_BASE, 24100.0)

    return tick, oi, mkt, expiry


def test_build_returns_india_context() -> None:
    tick, oi, mkt, expiry = _make_stores()
    builder = IndiaContextBuilder(tick, oi, mkt, expiry)
    builder.set_prev_day(_SYM, high=24200.0, low=23800.0, close=24000.0)

    ctx = builder.build(_SYM, _BASE, _ist(11, 0))

    assert ctx.base == _BASE
    assert ctx.symbol == _SYM
    assert len(ctx.candles_5m) > 0
    assert len(ctx.candles_15m) > 0
    assert len(ctx.candles_60m) > 0
    assert ctx.india_vix == 16.5
    assert ctx.max_pain_strike == 24100.0
    assert ctx.prev_day_high == 24200.0
    assert ctx.prev_day_low == 23800.0
    assert ctx.prev_day_close == 24000.0
    assert ctx.current_oi == 5_000_000.0
    assert not ctx.pcr_is_extreme_bearish
    assert not ctx.pcr_is_extreme_bullish
    assert ctx.pcr == 0.9  # 900k puts / 1M calls — raw PCR wired for the ledger
    assert ctx.tick_size == 0.05


def test_build_scan_time_ist_set() -> None:
    tick, oi, mkt, expiry = _make_stores()
    builder = IndiaContextBuilder(tick, oi, mkt, expiry)

    ctx = builder.build(_SYM, _BASE, _ist(11, 30))

    assert ctx.scan_time_ist is not None
    assert ctx.scan_time_ist.hour == 11
    assert ctx.scan_time_ist.minute == 30


def test_build_defaults_when_no_prev_day() -> None:
    tick, oi, mkt, expiry = _make_stores()
    builder = IndiaContextBuilder(tick, oi, mkt, expiry)

    ctx = builder.build(_SYM, _BASE, _ist(11, 0))

    assert ctx.prev_day_high == 0.0
    assert ctx.prev_day_low == 0.0
    assert ctx.prev_day_close == 0.0


def test_build_atr_from_tick_store() -> None:
    tick, oi, mkt, expiry = _make_stores()
    builder = IndiaContextBuilder(tick, oi, mkt, expiry)

    ctx = builder.build(_SYM, _BASE, _ist(11, 0))

    assert ctx.atr14_5m > 0.0


def test_build_volume_averages() -> None:
    tick, oi, mkt, expiry = _make_stores()
    builder = IndiaContextBuilder(tick, oi, mkt, expiry)

    ctx = builder.build(_SYM, _BASE, _ist(11, 0))

    assert ctx.volume_avg_5m_20 > 0.0


def test_regime_60m_forms_from_seeded_htf() -> None:
    """With enough seeded 60m history the regime classifies a real trend
    instead of defaulting to RANGING (needs EMA55 -> >=56 bars). Before the
    seed carried only ~6 60m bars and regime_60m was permanently RANGING."""
    from src.regime import Regime

    tick = IndiaTickStore()
    oi = IndiaOIStore()
    mkt = IndiaMarketData()
    expiry = ExpiryManager()

    # A steady 60-bar 60m uptrend with ATR% above the QUIET floor.
    c60 = [
        Candle(
            ts=_ist(9, 15) + timedelta(hours=i),
            open=24000.0 + 40 * i,
            high=24030.0 + 40 * i,
            low=23970.0 + 40 * i,
            close=24000.0 + 40 * i,
            volume=1000.0,
        )
        for i in range(60)
    ]
    c5 = [
        Candle(ts=_ist(9, 15 + 5 * i), open=25000.0, high=25010.0,
               low=24990.0, close=25000.0, volume=1000.0)
        for i in range(30)
    ]
    tick.seed(_SYM, c5, None, c60)

    builder = IndiaContextBuilder(tick, oi, mkt, expiry)
    ctx = builder.build(_SYM, _BASE, _ist(11, 0))

    assert len(ctx.candles_60m) >= 56
    assert ctx.regime_60m == Regime.TRENDING_UP


def test_build_pcr_zero_when_never_polled() -> None:
    # A never-polled (or stale) chain reads 0.0 — "unavailable", not a ratio.
    tick, _, mkt, expiry = _make_stores()
    fresh_oi = IndiaOIStore()
    builder = IndiaContextBuilder(tick, fresh_oi, mkt, expiry)
    ctx = builder.build(_SYM, _BASE, _ist(11, 0))
    assert ctx.pcr == 0.0


def test_refresh_daily_regime_folds_running_bar() -> None:
    """B4 fix: a range-morning that develops into a trend must be able to
    re-classify same-day (default OFF; this exercises the mechanism)."""
    from src.regime import Regime

    tick, oi, mkt, expiry = _make_stores()
    builder = IndiaContextBuilder(tick, oi, mkt, expiry)

    # ~200 flat daily bars -> RANGING seed.
    daily_base = IST.localize(datetime(2025, 9, 1))
    flat = [
        Candle(ts=daily_base + timedelta(days=i), open=24000.0, high=24050.0,
               low=23950.0, close=24000.0, volume=1000)
        for i in range(200)
    ]
    builder.set_daily_regime(_SYM, Regime.RANGING)

    # Without the daily series the refresh is a no-op (label unchanged).
    assert builder.refresh_daily_regime(_SYM, _ist(11, 0)) is None
    builder.set_daily_candles(_SYM, flat)

    # A monster up-day: running bar far above the flat series.
    tick.on_tick(_SYM, 24000.0, 10.0, _ist(9, 15))
    tick.on_tick(_SYM, 26400.0, 10.0, _ist(11, 0))  # +10% intraday
    result = builder.refresh_daily_regime(_SYM, _ist(11, 0))
    assert result is not None
    # And the next build() reads the refreshed label.
    ctx = builder.build(_SYM, _BASE, _ist(11, 0))
    assert ctx.regime_daily == result
