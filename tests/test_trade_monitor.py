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
    OUTCOME_TP1_BE,
    OUTCOME_TP1_EXPIRED,
    OUTCOME_TP2,
    IndiaTradeMonitor,
)

_SYM = "NSE:NIFTY26JULFUT"


def _now() -> datetime:
    return config.IST.localize(datetime(2026, 7, 6, 10, 0))


def _make_signal(direction: str = "LONG", tp2: float = 0.0) -> IndiaSignal:
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
        tp2=tp2,
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


# ── Two-target plan (Session 18): TP1 banked -> BE stop -> TP2 runner ──
#
# Fixture geometry (LONG): entry 24500, sl 24450, tp1 24600, tp2 24700.
# Cost-covering BE = entry + 0.06% = 24514.7.

_BE_LONG = 24500.0 + 24500.0 * 0.06 / 100.0  # 24514.7


def test_two_leg_tp1_then_be_stop() -> None:
    store = IndiaTickStore()
    monitor = IndiaTradeMonitor(store)
    monitor.register([_make_signal("LONG", tp2=24700.0)], _now())
    _seed(store, [
        (5, 24610.0, 24520.0, 24605.0),   # TP1 touched — runner armed
        (10, 24620.0, 24510.0, 24515.0),  # BE (24514.7) touched
    ])

    outcomes = monitor.check(_now() + timedelta(minutes=15))
    assert len(outcomes) == 1
    oc = outcomes[0]
    assert oc.outcome == OUTCOME_TP1_BE
    # 50% banked at TP1 (+100) + 50% scratched at BE (+14.7).
    assert round(oc.points, 2) == round(0.5 * 100.0 + 0.5 * (_BE_LONG - 24500.0), 2)
    assert oc.exit_price == _BE_LONG
    assert monitor.open_count == 0


def test_two_leg_tp1_then_tp2() -> None:
    store = IndiaTickStore()
    monitor = IndiaTradeMonitor(store)
    monitor.register([_make_signal("LONG", tp2=24700.0)], _now())
    _seed(store, [
        (5, 24610.0, 24520.0, 24605.0),   # TP1 touched
        (10, 24710.0, 24590.0, 24705.0),  # TP2 touched
    ])

    outcomes = monitor.check(_now() + timedelta(minutes=15))
    oc = outcomes[0]
    assert oc.outcome == OUTCOME_TP2
    assert oc.points == 0.5 * 100.0 + 0.5 * 200.0  # 150 blended
    assert oc.exit_price == 24700.0


def test_two_leg_tp1_then_session_close() -> None:
    store = IndiaTickStore()
    monitor = IndiaTradeMonitor(store)
    monitor.register([_make_signal("LONG", tp2=24700.0)], _now())
    _seed(store, [
        (5, 24610.0, 24520.0, 24605.0),   # TP1 touched
        (10, 24620.0, 24540.0, 24550.0),  # runner still open at close
    ])

    assert monitor.check(_now() + timedelta(minutes=15)) == []
    outcomes = monitor.force_close_all(_now() + timedelta(hours=5))
    oc = outcomes[0]
    assert oc.outcome == OUTCOME_TP1_EXPIRED
    assert oc.points == 0.5 * 100.0 + 0.5 * 50.0  # runner scored at last close


def test_two_leg_same_candle_sl_still_wins() -> None:
    store = IndiaTickStore()
    monitor = IndiaTradeMonitor(store)
    monitor.register([_make_signal("LONG", tp2=24700.0)], _now())
    # One wide candle spans SL and TP1 — conservative rule unchanged.
    _seed(store, [(5, 24650.0, 24400.0, 24500.0)])

    outcomes = monitor.check(_now() + timedelta(minutes=10))
    assert outcomes[0].outcome == OUTCOME_SL
    assert outcomes[0].points == -50.0  # full loss, no leg banked


def test_two_leg_runner_race_starts_next_candle() -> None:
    # The TP1-touch candle's own low pierces BE, but its intrabar sequence is
    # unknowable — the runner must NOT resolve on the touch candle.
    store = IndiaTickStore()
    monitor = IndiaTradeMonitor(store)
    monitor.register([_make_signal("LONG", tp2=24700.0)], _now())
    _seed(store, [(5, 24610.0, 24505.0, 24605.0)])  # touches TP1 AND trades below BE

    assert monitor.check(_now() + timedelta(minutes=10)) == []
    assert monitor.open_count == 1  # runner armed, still live
    marks = monitor.drain_tp1_marks()
    assert len(marks) == 1 and marks[0][0] == "sig-LONG"
    assert monitor.drain_tp1_marks() == []  # drained


def test_two_leg_short_mirrored() -> None:
    store = IndiaTickStore()
    monitor = IndiaTradeMonitor(store)
    monitor.register([_make_signal("SHORT", tp2=24300.0)], _now())
    _seed(store, [
        (5, 24510.0, 24390.0, 24410.0),   # TP1 (24400) touched
        (10, 24350.0, 24290.0, 24300.0),  # TP2 touched
    ])

    outcomes = monitor.check(_now() + timedelta(minutes=15))
    oc = outcomes[0]
    assert oc.outcome == OUTCOME_TP2
    assert oc.points == 0.5 * 100.0 + 0.5 * 200.0


def test_two_leg_be_without_cost_buffer(monkeypatch) -> None:
    monkeypatch.setattr(config, "BE_COST_BUFFER", False)
    store = IndiaTickStore()
    monitor = IndiaTradeMonitor(store)
    monitor.register([_make_signal("LONG", tp2=24700.0)], _now())
    _seed(store, [
        (5, 24610.0, 24520.0, 24605.0),   # TP1 touched
        (10, 24620.0, 24505.0, 24510.0),  # below cost-BE but above entry — holds
        (15, 24620.0, 24499.0, 24505.0),  # entry (24500) touched
    ])

    outcomes = monitor.check(_now() + timedelta(minutes=20))
    oc = outcomes[0]
    assert oc.outcome == OUTCOME_TP1_BE
    assert oc.exit_price == 24500.0
    assert oc.points == 0.5 * 100.0  # scratch runner at exact entry


def test_resume_restores_armed_runner() -> None:
    # A banked TP1 must survive a restart — the resumed signal goes straight
    # to the BE/TP2 race instead of re-racing SL vs TP1.
    store = IndiaTickStore()
    monitor = IndiaTradeMonitor(store)
    monitor.resume(
        [
            {
                "signal_id": "sig-armed",
                "symbol": _SYM,
                "direction": "LONG",
                "entry": 24500.0,
                "sl": 24450.0,
                "tp1": 24600.0,
                "tp2": 24700.0,
                "created_at": "2026-07-06 09:40:00",
                "tp1_touched_at": "2026-07-06 09:55:00",
            }
        ],
        _now(),
    )
    # SL-depth candle after the restart: an unarmed signal would score SL_HIT,
    # the armed runner scores TP1_BE.
    _seed(store, [(5, 24520.0, 24440.0, 24450.0)])
    outcomes = monitor.check(_now() + timedelta(minutes=10))
    assert outcomes[0].outcome == OUTCOME_TP1_BE


def test_legacy_signal_without_tp2_single_target() -> None:
    # tp2 == 0 (historical rows / INDIA_TP2_ENABLED=false) — old plan exactly.
    store = IndiaTickStore()
    monitor = IndiaTradeMonitor(store)
    monitor.register([_make_signal("LONG")], _now())
    _seed(store, [(5, 24610.0, 24490.0, 24605.0)])

    outcomes = monitor.check(_now() + timedelta(minutes=10))
    assert outcomes[0].outcome == OUTCOME_TP1
    assert outcomes[0].points == 100.0
