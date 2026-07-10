"""Session-17 signal-quality improvements, driven by the first clean
post-watchdog half-day (2026-07-10, 88 signals, 62 resolved).

Covers: the pattern-bar discipline (forming-bar reclaims/rejections wait for
a near-final bar), the ATR-scaled trigger floor (doji-sized "rejections"
no longer qualify), SRF's mapped-destination requirement (no more synthetic
1.5R fallback targets), the weekly-expiry scoping (NSE weekly options are
NIFTY-only since SEBI's one-weekly-per-exchange rule), and the VWAP / bar
freshness wiring in the context builder.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import config
from src.channels.india_scalp import (
    DivergenceContinuation,
    LiquiditySweepReversal,
    SrFlipRetest,
)
from src.data.india_context_builder import IndiaContextBuilder
from src.data.india_market_data import IndiaMarketData
from src.data.india_oi_store import IndiaOIStore
from src.data.india_tick_store import IndiaTickStore
from src.market.candle import Candle
from src.patterns import is_bearish_rejection, is_bullish_rejection
from src.regime import Regime
from src.session.expiry_manager import ExpiryManager
from tests.candle_factory import c, from_closes
from tests.signal_factory import make_context

IST = config.IST
_SYM = "NSE:NIFTY26JULFUT"


# ── ATR-scaled rejection trigger ─────────────────────────────────────


def test_rejection_rejects_doji_sized_bar() -> None:
    # A textbook pin shape, but only 4.4 points tall — noise, not rejection.
    pin = c(high=23984.0, low=23979.6, close=23983.0, open_=23982.0)
    assert is_bullish_rejection(pin, None) is True
    assert is_bullish_rejection(pin, None, min_range=50.0) is False


def test_rejection_accepts_full_size_bar() -> None:
    pin = c(high=23984.0, low=23925.0, close=23982.0, open_=23978.0)
    assert is_bullish_rejection(pin, None, min_range=50.0) is True


def test_bearish_rejection_min_range_mirrors() -> None:
    pin = c(high=23988.0, low=23983.0, close=23984.0, open_=23985.0)
    assert is_bearish_rejection(pin, None) is True
    assert is_bearish_rejection(pin, None, min_range=50.0) is False


# ── Pattern-bar discipline (forming-bar flicker) ─────────────────────


def _lsr_sweep_context(**over: object):  # type: ignore[no-untyped-def]
    # 15m series with a clear swing low at 23900; the 5m bar sweeps below it
    # and closes back above on volume — a valid LSR long when the bar is final.
    c15 = from_closes([24000.0, 23980.0, 23900.0, 23970.0, 23990.0, 24010.0])
    # Sweep bar carries 2x average volume so the LSR volume gate passes.
    sweep = c(high=23960.0, low=23880.0, close=23950.0, open_=23930.0, volume=2000.0)
    prev = c(high=23955.0, low=23935.0, close=23945.0)
    defaults = dict(
        candles_5m=[prev, sweep],
        candles_15m=c15,
        atr14_5m=30.0,
        volume_avg_5m_20=1000.0,
    )
    defaults.update(over)
    return make_context(**defaults)  # type: ignore[arg-type]


def test_lsr_emits_on_final_bar() -> None:
    sig = LiquiditySweepReversal().evaluate(_lsr_sweep_context())
    assert sig is not None


def test_lsr_waits_for_forming_bar() -> None:
    ctx = _lsr_sweep_context(bar_elapsed_fraction=0.2)
    assert LiquiditySweepReversal().evaluate(ctx) is None


def test_lsr_rejects_noise_sized_sweep_bar() -> None:
    # Same setup but the sweep bar is a sliver relative to ATR.
    small = c(high=23902.0, low=23890.0, close=23901.0, open_=23895.0)
    ctx = _lsr_sweep_context(candles_5m=[small, small], atr14_5m=100.0)
    assert LiquiditySweepReversal().evaluate(ctx) is None


def test_div_waits_for_forming_bar() -> None:
    ctx = make_context(bar_elapsed_fraction=0.2)
    assert DivergenceContinuation().evaluate(ctx) is None


# ── SRF: mapped destination required ─────────────────────────────────

_C15_SRF = (
    from_closes([24010.0, 24020.0, 24030.0, 24015.0, 24005.0])
    + from_closes([23990.0, 23970.0, 23930.0, 23960.0, 23980.0])
    + from_closes([23992.0])
)


def _srf_ctx(prev_day_low: float):  # type: ignore[no-untyped-def]
    prev_bar = c(high=23998.0, low=23990.0, close=23996.0, open_=23991.0)
    retest_bar = c(high=24060.0, low=23990.0, close=23992.0, open_=23994.0)
    return make_context(
        regime_60m=Regime.TRENDING_DOWN,
        candles_5m=[prev_bar, retest_bar],
        candles_15m=_C15_SRF,
        atr14_5m=100.0,
        prev_day_high=24200.0,
        prev_day_low=prev_day_low,
        prev_day_close=24000.0,
    )


def test_srf_uses_far_book_level_as_target() -> None:
    # prev_day_low 23800 sits beyond min-RR distance — a real destination.
    sig = SrFlipRetest().evaluate(_srf_ctx(prev_day_low=23800.0))
    assert sig is not None
    assert sig.tp1 == 23800.0
    assert sig.rr_ratio >= config.SRF_MIN_RR


def test_srf_silent_when_no_level_clears_min_rr(monkeypatch) -> None:
    # Push every mapped level inside min-RR distance: with the book-target
    # requirement (default) the candidate is rejected, not given a synthetic
    # 1.5R fallback (live 2026-07-10: 26/26 SRF signals were fallbacks, net
    # negative at 30% of the day's volume).
    sig = SrFlipRetest().evaluate(_srf_ctx(prev_day_low=23930.0))
    assert sig is None

    monkeypatch.setattr(config, "SRF_REQUIRE_BOOK_TARGET", False)
    relaxed = SrFlipRetest().evaluate(_srf_ctx(prev_day_low=23930.0))
    assert relaxed is not None  # legacy fallback still available via config


# ── Context builder: bar freshness, VWAP, weekly-expiry scoping ──────


def _builder(store: IndiaTickStore) -> IndiaContextBuilder:
    return IndiaContextBuilder(
        store, IndiaOIStore(), IndiaMarketData(), ExpiryManager()
    )


def _candle(ts: datetime, price: float, volume: float = 1000.0) -> Candle:
    return Candle(
        ts=ts, open=price, high=price, low=price, close=price, volume=volume
    )


def test_builder_stamps_bar_elapsed_fraction() -> None:
    now = IST.localize(datetime(2026, 7, 10, 11, 2, 30))
    bar_open = IST.localize(datetime(2026, 7, 10, 11, 0, 0))
    store = IndiaTickStore()
    store.seed(_SYM, [_candle(bar_open, 24000.0)])
    ctx = _builder(store).build(_SYM, "NIFTY", now)
    assert abs(ctx.bar_elapsed_fraction - 0.5) < 0.01  # 150s of 300s


def test_builder_completed_bar_reads_full() -> None:
    now = IST.localize(datetime(2026, 7, 10, 11, 30, 0))
    old_bar = IST.localize(datetime(2026, 7, 10, 11, 0, 0))
    store = IndiaTickStore()
    store.seed(_SYM, [_candle(old_bar, 24000.0)])
    ctx = _builder(store).build(_SYM, "NIFTY", now)
    assert ctx.bar_elapsed_fraction == 1.0


def test_builder_wires_session_vwap_into_levels() -> None:
    now = IST.localize(datetime(2026, 7, 10, 11, 30, 0))
    t0 = IST.localize(datetime(2026, 7, 10, 10, 0, 0))
    store = IndiaTickStore()
    store.seed(
        _SYM,
        [
            _candle(t0, 24000.0, volume=1000.0),
            _candle(t0 + timedelta(minutes=5), 24100.0, volume=3000.0),
        ],
    )
    ctx = _builder(store).build(_SYM, "NIFTY", now)
    assert len(ctx.key_levels_extra) == 1
    vwap = ctx.key_levels_extra[0]
    assert vwap == (24000.0 * 1000 + 24100.0 * 3000) / 4000


def test_builder_vwap_ignores_prior_day_bars() -> None:
    now = IST.localize(datetime(2026, 7, 10, 11, 30, 0))
    yday = IST.localize(datetime(2026, 7, 9, 14, 0, 0))
    store = IndiaTickStore()
    store.seed(_SYM, [_candle(yday, 20000.0)])
    ctx = _builder(store).build(_SYM, "NIFTY", now)
    assert ctx.key_levels_extra == []


def test_weekly_expiry_flag_is_nifty_only() -> None:
    # 2026-07-14 is a Tuesday but NOT July's last Tuesday (2026-07-28):
    # NIFTY (weekly options) is on expiry; BANKNIFTY (monthly-only since
    # SEBI's one-weekly-per-exchange rule) is not.
    tuesday = IST.localize(datetime(2026, 7, 14, 11, 0, 0))
    store = IndiaTickStore()
    store.seed(_SYM, [_candle(tuesday - timedelta(minutes=5), 24000.0)])
    builder = _builder(store)
    assert builder.build(_SYM, "NIFTY", tuesday).is_expiry_day is True
    assert (
        builder.build("NSE:BANKNIFTY26JULFUT", "BANKNIFTY", tuesday).is_expiry_day
        is False
    )


def test_monthly_expiry_day_flags_all_bases() -> None:
    last_tuesday = IST.localize(datetime(2026, 7, 28, 11, 0, 0))
    store = IndiaTickStore()
    builder = _builder(store)
    assert (
        builder.build("NSE:BANKNIFTY26JULFUT", "BANKNIFTY", last_tuesday).is_expiry_day
        is True
    )
