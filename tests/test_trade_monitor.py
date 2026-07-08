"""Trade monitor — TP1/SL/expiry resolution semantics."""

from __future__ import annotations

from datetime import datetime, timedelta

import config
from src.data.india_tick_store import IndiaTickStore
from src.market.candle import Candle
from src.signals.model import IndiaSignal
from src.trade_monitor import (
    OUTCOME_EXPIRED,
    OUTCOME_SL,
    OUTCOME_TP1,
    IndiaTradeMonitor,
)

_SYM = "NSE:NIFTY26JULFUT"


def _now() -> datetime:
    return config.IST.localize(datetime(2026, 7, 6, 10, 0))


def _make_signal(direction: str = "LONG") -> IndiaSignal:
    sl, tp1 = (24450.0, 24600.0) if direction == "LONG" else (24550.0, 24400.0)
    return IndiaSignal(
        signal_id=f"sig-{direction}",
        symbol=_SYM,
        base="NIFTY",
        direction=direction,
        setup_class="OPENING_RANGE_BREAKOUT",
        entry=24500.0,
        sl=sl,
        tp1=tp1,
        sl_pct=0.2,
        tp1_pct=0.4,
        rr_ratio=2.0,
        lot_size=75,
        htf_trend_aligned=True,
        breakout_volume_ratio=1.5,
        setup_reason="test",
        regime_60m="TRENDING_UP",
        regime_daily="RANGING",
        atr_at_entry=20.0,
        vix_at_entry=14.0,
        pcr_at_entry=1.0,
        expiry_date=None,
        days_to_expiry=3,
        dispatch_timestamp=0.0,
    )


def _seed(store: IndiaTickStore, bars: list[tuple[int, float, float, float]]) -> None:
    """bars: (minutes_after_now, high, low, close)."""
    base_ts = _now()
    candles = [
        Candle(
            ts=base_ts + timedelta(minutes=m),
            open=c,
            high=h,
            low=lo,
            close=c,
            volume=1000,
        )
        for m, h, lo, c in bars
    ]
    store.seed(_SYM, candles)


def test_long_tp1_hit() -> None:
    store = IndiaTickStore()
    monitor = IndiaTradeMonitor(store)
    monitor.register([_make_signal("LONG")], _now())
    _seed(store, [(5, 24610.0, 24490.0, 24605.0)])

    outcomes = monitor.check(_now() + timedelta(minutes=10))
    assert len(outcomes) == 1
    assert outcomes[0].outcome == OUTCOME_TP1
    assert outcomes[0].points == 100.0
    # Signed % return = points / entry — the cross-instrument-comparable measure.
    assert round(outcomes[0].pct, 4) == round(100.0 / 24500.0 * 100.0, 4)
    assert monitor.open_count == 0


def test_long_sl_hit_negative_points() -> None:
    store = IndiaTickStore()
    monitor = IndiaTradeMonitor(store)
    monitor.register([_make_signal("LONG")], _now())
    _seed(store, [(5, 24510.0, 24440.0, 24460.0)])

    outcomes = monitor.check(_now() + timedelta(minutes=10))
    assert outcomes[0].outcome == OUTCOME_SL
    assert outcomes[0].points == -50.0


def test_same_candle_touching_both_resolves_to_sl() -> None:
    store = IndiaTickStore()
    monitor = IndiaTradeMonitor(store)
    monitor.register([_make_signal("LONG")], _now())
    # One wide candle spans both SL and TP1 — conservative rule applies.
    _seed(store, [(5, 24650.0, 24400.0, 24500.0)])

    outcomes = monitor.check(_now() + timedelta(minutes=10))
    assert outcomes[0].outcome == OUTCOME_SL


def test_short_tp1_hit() -> None:
    store = IndiaTickStore()
    monitor = IndiaTradeMonitor(store)
    monitor.register([_make_signal("SHORT")], _now())
    _seed(store, [(5, 24510.0, 24390.0, 24410.0)])

    outcomes = monitor.check(_now() + timedelta(minutes=10))
    assert outcomes[0].outcome == OUTCOME_TP1
    assert outcomes[0].points == 100.0


def test_untouched_stays_open_then_expires_at_close() -> None:
    store = IndiaTickStore()
    monitor = IndiaTradeMonitor(store)
    monitor.register([_make_signal("LONG")], _now())
    _seed(store, [(5, 24520.0, 24480.0, 24515.0)])

    assert monitor.check(_now() + timedelta(minutes=10)) == []
    assert monitor.open_count == 1

    outcomes = monitor.force_close_all(_now() + timedelta(hours=5))
    assert outcomes[0].outcome == OUTCOME_EXPIRED
    assert outcomes[0].exit_price == 24515.0
    assert outcomes[0].points == 15.0
    assert monitor.open_count == 0


def test_candles_before_registration_ignored() -> None:
    store = IndiaTickStore()
    monitor = IndiaTradeMonitor(store)
    # TP-touching candle exists BEFORE the signal was registered.
    _seed(store, [(-10, 24650.0, 24580.0, 24590.0)])
    monitor.register([_make_signal("LONG")], _now())

    assert monitor.check(_now() + timedelta(minutes=5)) == []


def test_resume_re_tracks_rows() -> None:
    store = IndiaTickStore()
    monitor = IndiaTradeMonitor(store)
    monitor.resume(
        [
            {
                "signal_id": "sig-old",
                "symbol": _SYM,
                "direction": "LONG",
                "entry": 24500.0,
                "sl": 24450.0,
                "tp1": 24600.0,
                "created_at": "2026-07-06 09:40:00",
            }
        ],
        _now(),
    )
    assert monitor.open_count == 1
    _seed(store, [(5, 24610.0, 24490.0, 24605.0)])
    outcomes = monitor.check(_now() + timedelta(minutes=10))
    assert outcomes[0].outcome == OUTCOME_TP1
