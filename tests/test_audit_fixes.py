"""Session-9 audit fixes — regression coverage.

Covers: cumulative-volume deltas, intraday-state seeding, IB11 min-scalp
gate, stock-scaled thresholds, IB13 macro-event gate, IB16 expiry floor
bump, per-scan/per-day emission caps, per-base PCR aggregation, corrected
max-pain, Fyers v3 flat option-chain parsing, and daily-regime wiring.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import config
from src.broker.history_utils import CumulativeVolume
from src.data.india_context_builder import IndiaContextBuilder
from src.data.india_market_data import IndiaMarketData
from src.data.india_oi_store import IndiaOIStore
from src.data.india_tick_store import IndiaTickStore
from src.market.candle import Candle
from src.regime import Regime
from src.scanner import GateChain
from src.session.event_calendar import EventCalendar
from src.session.expiry_manager import ExpiryManager
from src.session.session_manager import SessionState
from tests.signal_factory import make_context, make_signal

IST = config.IST
_SYM = "NSE:NIFTY26JULFUT"


def _ist(h: int, m: int) -> datetime:
    return IST.localize(datetime(2026, 7, 8, h, m))  # Wednesday (not expiry)


def _candle(h: int, m: int, o=100.0, hi=101.0, lo=99.0, c=100.5, v=1000.0) -> Candle:
    return Candle(ts=_ist(h, m), open=o, high=hi, low=lo, close=c, volume=v)


# ── CumulativeVolume ────────────────────────────────────────────────────


def test_cumvol_first_observation_is_baseline() -> None:
    cv = CumulativeVolume()
    assert cv.delta(_SYM, 80_000_000.0, _ist(12, 0)) == 0.0


def test_cumvol_same_day_increment() -> None:
    cv = CumulativeVolume()
    cv.delta(_SYM, 100.0, _ist(10, 0))
    assert cv.delta(_SYM, 150.0, _ist(10, 1)) == 50.0
    assert cv.delta(_SYM, 150.0, _ist(10, 2)) == 0.0


def test_cumvol_new_day_returns_reading() -> None:
    cv = CumulativeVolume()
    cv.delta(_SYM, 90_000_000.0, _ist(15, 29))
    next_day = _ist(9, 15) + timedelta(days=1)
    assert cv.delta(_SYM, 500.0, next_day) == 500.0


def test_cumvol_decrease_rebaselines_to_zero() -> None:
    cv = CumulativeVolume()
    cv.delta(_SYM, 1000.0, _ist(10, 0))
    assert cv.delta(_SYM, 400.0, _ist(10, 1)) == 0.0
    assert cv.delta(_SYM, 450.0, _ist(10, 2)) == 50.0


# ── Intraday-state seeding (mid-session restart) ────────────────────────


def test_seed_intraday_state_rebuilds_or_and_day_open() -> None:
    store = IndiaTickStore()
    todays = [
        _candle(9, 15, o=100.0, hi=104.0, lo=98.0),
        _candle(9, 30, hi=106.0, lo=99.0),
        _candle(9, 45, hi=110.0, lo=97.0),  # outside the 09:15–09:45 OR window
        _candle(11, 0, hi=112.0, lo=96.0),
    ]
    now = _ist(11, 5)
    store.seed(_SYM, todays)
    store.seed_intraday_state(_SYM, todays, now)

    assert store.get_day_open(_SYM) == 100.0
    assert store.get_intraday_high(_SYM) == 112.0
    assert store.get_intraday_low(_SYM) == 96.0
    or_high, or_low = store.get_opening_range(_SYM)
    assert or_high == 106.0  # 09:45 bar excluded from OR
    assert or_low == 98.0
    # OR must be locked — a later tick may not move it.
    store.on_tick(_SYM, 200.0, 10.0, _ist(11, 6))
    assert store.get_opening_range(_SYM) == (106.0, 98.0)
    # ...and the live tick must not reset day_open (same IST date).
    assert store.get_day_open(_SYM) == 100.0


def test_seed_intraday_state_noop_without_todays_candles() -> None:
    store = IndiaTickStore()
    store.seed_intraday_state(_SYM, [], _ist(9, 20))
    assert store.get_day_open(_SYM) == 0.0


# ── IB11 min-scalp gate ─────────────────────────────────────────────────


def test_min_scalp_gate_suppresses_sub_viable_index_target() -> None:
    chain = GateChain()
    # NIFTY floor is 15 points — a 10-point TP1 can't pay its own STT.
    sig = make_signal(entry=24000.0, sl=23990.0, tp1=24010.0)
    ctx = make_context(atr14_5m=10.0)
    result = chain.check(sig, ctx, SessionState.OPEN, _ist(10, 0))
    assert result == "min_scalp_gate"


def test_min_scalp_gate_passes_viable_index_target() -> None:
    chain = GateChain()
    sig = make_signal(entry=24000.0, sl=23985.0, tp1=24030.0)
    ctx = make_context(atr14_5m=10.0)
    result = chain.check(sig, ctx, SessionState.OPEN, _ist(10, 0))
    assert result is None


def test_min_scalp_points_for_stock_scales_by_price() -> None:
    # 0.10% of a ₹500 stock = 0.5 points.
    assert config.min_scalp_points_for("RELIANCE", 500.0) == 0.5
    assert config.min_scalp_points_for("NIFTY", 24000.0) == 15.0
    assert config.min_scalp_points_for("BANKNIFTY", 52000.0) == 40.0


# ── Stock-scaled thresholds ─────────────────────────────────────────────


def test_round_step_for_price_bands() -> None:
    assert config.round_step_for("NIFTY", 24000.0) == 50.0
    assert config.round_step_for("BANKNIFTY", 52000.0) == 100.0
    assert config.round_step_for("SAIL", 120.0) == 1.0
    assert config.round_step_for("SBIN", 600.0) == 5.0
    assert config.round_step_for("RELIANCE", 1300.0) == 10.0
    assert config.round_step_for("MARUTI", 12000.0) == 100.0


def test_min_atr_gate_scales_for_stocks() -> None:
    chain = GateChain()
    # ₹500 stock with 0.4-point 5m ATR (0.08%) is tradeable; the old 3-point
    # absolute floor would have suppressed it forever.
    sig = make_signal(base="SBIN", entry=500.0, sl=499.0, tp1=502.0)
    candles = [
        Candle(ts=_ist(10, 0), open=500.0, high=500.5, low=499.5,
               close=500.0, volume=1000.0)
    ]
    ctx = make_context(base="SBIN", atr14_5m=0.4)
    object.__setattr__(ctx, "candles_5m", candles)
    result = chain.check(sig, ctx, SessionState.OPEN, _ist(10, 0))
    assert result is None


def test_min_atr_gate_still_suppresses_dead_stock_tape() -> None:
    chain = GateChain()
    sig = make_signal(base="SBIN", entry=500.0, sl=499.0, tp1=502.0)
    candles = [
        Candle(ts=_ist(10, 0), open=500.0, high=500.1, low=499.9,
               close=500.0, volume=1000.0)
    ]
    ctx = make_context(base="SBIN", atr14_5m=0.1)  # 0.02% — dead
    object.__setattr__(ctx, "candles_5m", candles)
    result = chain.check(sig, ctx, SessionState.OPEN, _ist(10, 0))
    assert result == "min_atr_gate"


# ── IB13 macro-event gate ───────────────────────────────────────────────


def test_event_calendar_loads_shipped_file() -> None:
    cal = EventCalendar()
    assert cal.is_verified()
    assert cal.event_on("2026-08-05") is not None  # RBI MPC announcement


def test_event_gate_suppresses_on_macro_event_day(tmp_path) -> None:
    events = tmp_path / "events.json"
    events.write_text(
        json.dumps(
            {"verified": True, "events": {"2026-07-08": "RBI MPC (test)"}}
        )
    )
    chain = GateChain(events=EventCalendar(str(events)))
    sig = make_signal()
    ctx = make_context(atr14_5m=10.0)
    result = chain.check(sig, ctx, SessionState.OPEN, _ist(10, 0))
    assert result == "event_risk_gate"


# ── IB16 expiry-day confidence bump ─────────────────────────────────────


def test_confidence_floor_bumps_on_expiry_day() -> None:
    chain = GateChain()
    sig = make_signal()
    ctx = make_context(atr14_5m=10.0)
    object.__setattr__(ctx, "is_expiry_day", True)
    just_above_floor = config.CONFIDENCE_EMIT_FLOOR + 2.0
    result = chain.check_confidence_floor(sig, ctx, just_above_floor, _ist(10, 0))
    assert result == "confidence_floor_gate"
    # Same score passes on a normal day.
    object.__setattr__(ctx, "is_expiry_day", False)
    assert chain.check_confidence_floor(
        sig, ctx, just_above_floor, _ist(10, 5)
    ) is None


# ── Per-base PCR aggregation ────────────────────────────────────────────


def test_pcr_aggregates_across_index_bases() -> None:
    store = IndiaOIStore()
    store.update_pcr(600_000.0, 1_000_000.0, base="NIFTY")     # 0.6 alone
    store.update_pcr(1_500_000.0, 1_000_000.0, base="BANKNIFTY")  # 1.5 alone
    # Aggregate: 2.1M puts / 2.0M calls = 1.05 — neither extreme.
    assert abs(store.get_pcr() - 1.05) < 1e-9
    assert not store.is_pcr_extreme_bearish()
    assert not store.is_pcr_extreme_bullish()
    # Re-poll of one base replaces, not accumulates.
    store.update_pcr(400_000.0, 1_000_000.0, base="BANKNIFTY")
    assert abs(store.get_pcr() - 0.5) < 1e-9
    assert store.is_pcr_extreme_bearish()


# ── Corrected max-pain ──────────────────────────────────────────────────


def test_max_pain_prefers_strike_minimising_holder_payout() -> None:
    mkt = IndiaMarketData()
    # Huge call OI at 24000 means an expiry above 24000 pays call holders a
    # lot — max pain must stay AT/below the heavy call strike.
    strikes = [23900.0, 24000.0, 24100.0]
    call_oi = [0.0, 10_000_000.0, 0.0]
    put_oi = [0.0, 100_000.0, 0.0]
    best = mkt.compute_and_set_max_pain("NIFTY", strikes, call_oi, put_oi)
    # Payout at 23900: puts pay 100k×100 = 10M. At 24000: 0. At 24100:
    # calls pay 10M×100 = 1B. Correct answer: 24000.
    assert best == 24000.0


def test_max_pain_rejects_mismatched_lengths() -> None:
    mkt = IndiaMarketData()
    assert mkt.compute_and_set_max_pain("NIFTY", [1.0, 2.0], [1.0, 2.0], [1.0]) == 0.0


# ── Fyers v3 flat optionsChain parsing ──────────────────────────────────


def test_process_option_chain_parses_v3_flat_shape() -> None:
    from src.broker.fyers_feed import FyersDataFeed
    from src.session.expiry_manager import ExpiryManager as _EM

    tick, oi, mkt = IndiaTickStore(), IndiaOIStore(), IndiaMarketData()
    feed = FyersDataFeed(tick, oi, mkt, _EM())
    chain_data = {
        "callOi": 2_000_000.0,
        "putOi": 1_000_000.0,
        "optionsChain": [
            {"strike_price": -1, "option_type": "", "oi": 0},  # underlying row
            {"strike_price": 24000.0, "option_type": "CE", "oi": 1_200_000.0},
            {"strike_price": 24000.0, "option_type": "PE", "oi": 600_000.0},
            {"strike_price": 24100.0, "option_type": "CE", "oi": 800_000.0},
            {"strike_price": 24100.0, "option_type": "PE", "oi": 400_000.0},
        ],
    }
    feed._process_option_chain("NIFTY", chain_data)
    assert abs(oi.get_pcr() - 0.5) < 1e-9  # chain totals preferred
    assert oi.is_pcr_extreme_bearish()
    assert mkt.get_max_pain("NIFTY") is not None


# ── Daily regime wiring ─────────────────────────────────────────────────


def test_context_builder_uses_seeded_daily_regime() -> None:
    tick, oi, mkt = IndiaTickStore(), IndiaOIStore(), IndiaMarketData()
    builder = IndiaContextBuilder(tick, oi, mkt, ExpiryManager())
    tick.seed(_SYM, [_candle(10, 0)])
    builder.set_daily_regime(_SYM, Regime.TRENDING_UP)
    ctx = builder.build(_SYM, "NIFTY", _ist(10, 5))
    assert ctx.regime_daily is Regime.TRENDING_UP
    # Unknown symbol falls back to RANGING.
    tick.seed("NSE:X26JULFUT", [_candle(10, 0)])
    ctx2 = builder.build("NSE:X26JULFUT", "BANKNIFTY", _ist(10, 5))
    assert ctx2.regime_daily is Regime.RANGING


def test_stock_expiry_day_keys_off_monthly_contract() -> None:
    tick, oi, mkt = IndiaTickStore(), IndiaOIStore(), IndiaMarketData()
    builder = IndiaContextBuilder(tick, oi, mkt, ExpiryManager())
    tick.seed(_SYM, [_candle(10, 0)])
    # 2026-07-07 is a Tuesday (weekly index expiry) but NOT July's last
    # Tuesday (2026-07-28): index True, stock False.
    tuesday = IST.localize(datetime(2026, 7, 7, 10, 0))
    ctx_idx = builder.build(_SYM, "NIFTY", tuesday)
    assert ctx_idx.is_expiry_day is True
    ctx_stock = builder.build(_SYM, "RELIANCE", tuesday)
    assert ctx_stock.is_expiry_day is False
    # On the last Tuesday both are expiry days.
    last_tue = IST.localize(datetime(2026, 7, 28, 10, 0))
    ctx_stock2 = builder.build(_SYM, "RELIANCE", last_tue)
    assert ctx_stock2.is_expiry_day is True
