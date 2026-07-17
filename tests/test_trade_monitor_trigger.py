"""Entry-trigger state machine + 1m resolution + walk telemetry (Session 21).

The ledger-truth fixes: LEVEL entries (ORB/VSB/BDS) only start their SL/TP
race once price actually trades through the printed entry; outcomes resolve
on 1m candles when coverage allows (falling back to 5m per signal); every
outcome carries MFE/MAE, bars-to-resolve, the resolving timeframe and the
ambiguous-tie flag.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import config
from src.data.india_tick_store import IndiaTickStore
from src.market.candle import Candle
from src.signals.model import EntryType, IndiaSignal
from src.trade_monitor import (
    OUTCOME_NOT_TRIGGERED,
    OUTCOME_SL,
    OUTCOME_TP1_BE,
    OUTCOME_TP2,
    IndiaTradeMonitor,
)

_SYM = "NSE:NIFTY26JULFUT"


def _now() -> datetime:
    return config.IST.localize(datetime(2026, 7, 6, 10, 0))


def _level_signal(direction: str = "LONG") -> IndiaSignal:
    """A VSB-shaped LEVEL entry: entry above (long) the last price."""
    sl, tp1, tp2 = (
        (24450.0, 24600.0, 24700.0)
        if direction == "LONG"
        else (24550.0, 24400.0, 24300.0)
    )
    return IndiaSignal(
        signal_id=f"lvl-{direction}",
        symbol=_SYM,
        base="NIFTY",
        direction=direction,
        setup_class="VOLUME_SURGE_BREAKOUT",
        entry=24500.0,
        sl=sl,
        tp1=tp1,
        sl_pct=0.2,
        tp1_pct=0.4,
        rr_ratio=2.0,
        lot_size=65,
        tp2=tp2,
        entry_type=EntryType.LEVEL,
    )


def _seed_5m(
    store: IndiaTickStore, bars: list[tuple[int, float, float, float]]
) -> None:
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


def _ticks(
    store: IndiaTickStore, points: list[tuple[int, int, float]]
) -> None:
    """points: (minutes_after_now, seconds, price) — builds 1m (and 5m) bars."""
    base_ts = _now()
    for m, s, price in points:
        store.on_tick(_SYM, price, 10.0, base_ts + timedelta(minutes=m, seconds=s))


# ── trigger semantics ────────────────────────────────────────────────


def test_level_entry_fills_then_races_to_tp2() -> None:
    store = IndiaTickStore()
    monitor = IndiaTradeMonitor(store)
    monitor.register([_level_signal("LONG")], _now())
    # bar1 retraces through entry (fill, adverse-only check), bar2 touches
    # TP1 (runner arms), bar3 reaches TP2.
    _seed_5m(
        store,
        [
            (5, 24520.0, 24495.0, 24510.0),
            (10, 24605.0, 24505.0, 24600.0),
            (15, 24705.0, 24600.0, 24700.0),
        ],
    )
    outcomes = monitor.check(_now() + timedelta(minutes=20))
    assert len(outcomes) == 1
    oc = outcomes[0]
    assert oc.outcome == OUTCOME_TP2
    # Blend: 0.5 x (24600-24500) + 0.5 x (24700-24500) = 150
    assert oc.points == 150.0
    assert oc.bars_to_resolve == 3


def test_level_entry_persists_trigger_mark() -> None:
    store = IndiaTickStore()
    monitor = IndiaTradeMonitor(store)
    monitor.register([_level_signal("LONG")], _now())
    _seed_5m(store, [(5, 24520.0, 24495.0, 24510.0)])
    assert monitor.check(_now() + timedelta(minutes=10)) == []
    marks = monitor.drain_trigger_marks()
    assert len(marks) == 1
    assert marks[0][0] == "lvl-LONG"
    assert monitor.drain_trigger_marks() == []  # drained


def test_level_never_touched_tp_first_is_not_triggered() -> None:
    """The move runs to TP1 without ever giving the entry — no fill, no win."""
    store = IndiaTickStore()
    monitor = IndiaTradeMonitor(store)
    monitor.register([_level_signal("LONG")], _now())
    _seed_5m(store, [(5, 24610.0, 24505.0, 24605.0)])  # low never <= entry
    outcomes = monitor.check(_now() + timedelta(minutes=10))
    assert outcomes[0].outcome == OUTCOME_NOT_TRIGGERED
    assert outcomes[0].points == 0.0
    assert outcomes[0].pct == 0.0


def test_level_never_touched_sl_first_is_not_triggered() -> None:
    store = IndiaTickStore()
    monitor = IndiaTradeMonitor(store)
    sig = _level_signal("LONG")
    sig.entry = 24520.0  # entry above; price collapses straight to SL
    monitor.register([sig], _now())
    _seed_5m(store, [(5, 24510.0, 24440.0, 24450.0)])
    outcomes = monitor.check(_now() + timedelta(minutes=10))
    assert outcomes[0].outcome == OUTCOME_NOT_TRIGGERED


def test_level_expires_untriggered_after_deadline_candles() -> None:
    store = IndiaTickStore()
    monitor = IndiaTradeMonitor(store)
    sig = _level_signal("LONG")
    sig.entry = 24450.0  # never revisited
    sig.sl = 24400.0
    monitor.register([sig], _now())
    # Quiet drift above the entry for > ENTRY_TRIGGER_EXPIRY_MIN.
    bars = [
        (5 * i, 24560.0, 24505.0, 24550.0)
        for i in range(1, 3 + config.ENTRY_TRIGGER_EXPIRY_MIN // 5)
    ]
    _seed_5m(store, bars)
    outcomes = monitor.check(_now() + timedelta(minutes=60))
    assert outcomes[0].outcome == OUTCOME_NOT_TRIGGERED


def test_level_expires_untriggered_on_wall_clock_without_candles() -> None:
    store = IndiaTickStore()
    monitor = IndiaTradeMonitor(store)
    monitor.register([_level_signal("LONG")], _now())
    # No candles at all — thin tape. Deadline still enforced by `now`.
    late = _now() + timedelta(minutes=config.ENTRY_TRIGGER_EXPIRY_MIN + 5)
    outcomes = monitor.check(late)
    assert outcomes[0].outcome == OUTCOME_NOT_TRIGGERED


def test_force_close_pending_level_is_not_triggered() -> None:
    store = IndiaTickStore()
    monitor = IndiaTradeMonitor(store)
    monitor.register([_level_signal("LONG")], _now())
    _seed_5m(store, [(5, 24560.0, 24505.0, 24550.0)])
    assert monitor.check(_now() + timedelta(minutes=10)) == []
    outcomes = monitor.force_close_all(_now() + timedelta(minutes=15))
    assert outcomes[0].outcome == OUTCOME_NOT_TRIGGERED


def test_trigger_candle_sl_touch_is_conservative_sl() -> None:
    """The fill candle spans entry AND stop — adverse-first, SL_HIT."""
    store = IndiaTickStore()
    monitor = IndiaTradeMonitor(store)
    monitor.register([_level_signal("LONG")], _now())
    _seed_5m(store, [(5, 24520.0, 24440.0, 24460.0)])
    outcomes = monitor.check(_now() + timedelta(minutes=10))
    assert outcomes[0].outcome == OUTCOME_SL
    assert outcomes[0].ambiguous_tie is True


def test_tp_race_starts_after_trigger_candle() -> None:
    """The fill candle also spans TP1 — TP1 must NOT bank on it."""
    store = IndiaTickStore()
    monitor = IndiaTradeMonitor(store)
    monitor.register([_level_signal("LONG")], _now())
    # bar1 spans entry and TP1 (no SL) — fill only; bar2 quiet; runner never
    # arms off bar1's high.
    _seed_5m(
        store,
        [
            (5, 24650.0, 24495.0, 24560.0),
            (10, 24570.0, 24540.0, 24550.0),
        ],
    )
    assert monitor.check(_now() + timedelta(minutes=15)) == []
    tracked = list(monitor._open.values())[0]
    assert tracked.triggered_at is not None
    assert tracked.tp1_touched_at is None


def test_rewalk_after_trigger_ignores_pretrigger_candles() -> None:
    """Candles before the fill never enter the SL/TP race on a re-walk."""
    store = IndiaTickStore()
    monitor = IndiaTradeMonitor(store)
    sig = _level_signal("LONG")
    monitor.register([sig], _now())
    # bar1 pre-trigger, touches TP1 region? No — bar1 must be neutral but
    # spike ABOVE tp1 while never touching entry would cancel; instead bar1
    # dips toward (not through) SL, bar2 fills, bar3 quiet.
    _seed_5m(
        store,
        [
            (5, 24560.0, 24505.0, 24550.0),
            (10, 24520.0, 24495.0, 24510.0),
        ],
    )
    assert monitor.check(_now() + timedelta(minutes=12)) == []
    # Re-walk with one more quiet bar: the pre-trigger bar1 must not race.
    _ticks(store, [(15, 0, 24540.0), (15, 30, 24545.0)])
    assert monitor.check(_now() + timedelta(minutes=16)) == []
    tracked = list(monitor._open.values())[0]
    assert tracked.tp1_touched_at is None


def test_market_signal_legacy_semantics_unchanged() -> None:
    store = IndiaTickStore()
    monitor = IndiaTradeMonitor(store)
    sig = _level_signal("LONG")
    sig.entry_type = EntryType.MARKET
    monitor.register([sig], _now())
    # First candle races immediately — no trigger wait: TP1 banks, runner arms.
    _seed_5m(store, [(5, 24610.0, 24505.0, 24605.0)])
    assert monitor.check(_now() + timedelta(minutes=10)) == []
    tracked = list(monitor._open.values())[0]
    assert tracked.tp1_touched_at is not None


def test_flag_off_level_fills_at_emission(monkeypatch) -> None:
    monkeypatch.setattr(config, "ENTRY_TRIGGER_ENABLED", False)
    store = IndiaTickStore()
    monitor = IndiaTradeMonitor(store)
    monitor.register([_level_signal("LONG")], _now())
    _seed_5m(store, [(5, 24610.0, 24505.0, 24605.0)])
    assert monitor.check(_now() + timedelta(minutes=10)) == []
    # Legacy behaviour: TP1 banked without the entry ever trading.
    tracked = list(monitor._open.values())[0]
    assert tracked.tp1_touched_at is not None


def test_resume_restores_pending_and_triggered_states() -> None:
    store = IndiaTickStore()
    monitor = IndiaTradeMonitor(store)
    rows = [
        {
            "signal_id": "resume-pending",
            "symbol": _SYM,
            "direction": "LONG",
            "entry": 24500.0,
            "sl": 24450.0,
            "tp1": 24600.0,
            "tp2": 24700.0,
            "created_at": str(_now()),
            "entry_type": EntryType.LEVEL,
            "triggered_at": None,
        },
        {
            "signal_id": "resume-triggered",
            "symbol": _SYM,
            "direction": "LONG",
            "entry": 24500.0,
            "sl": 24450.0,
            "tp1": 24600.0,
            "tp2": 24700.0,
            "created_at": str(_now()),
            "entry_type": EntryType.LEVEL,
            "triggered_at": str(_now() + timedelta(minutes=5)),
        },
    ]
    monitor.resume(rows, _now() + timedelta(minutes=6))
    pending = monitor._open["resume-pending"]
    triggered = monitor._open["resume-triggered"]
    assert pending.triggered_at is None
    assert triggered.triggered_at is not None


# ── 1m resolution ────────────────────────────────────────────────────


def test_1m_walk_resolves_intrabar_order_5m_would_tie() -> None:
    """Within one 5m bucket: price goes UP through TP1 first, then falls to
    SL. A 5m walk sees one candle spanning both → conservative SL. The 1m
    walk sees the true order → TP1 banks, runner then stops at BE."""
    store = IndiaTickStore()
    monitor = IndiaTradeMonitor(store)
    sig = _level_signal("LONG")
    sig.entry_type = EntryType.MARKET  # isolate resolution semantics
    monitor.register([sig], _now())
    # Ticks from 30s after registration (1m coverage reaches back).
    _ticks(
        store,
        [
            (0, 30, 24510.0),
            (1, 10, 24560.0),
            (2, 10, 24605.0),  # TP1 touched in minute 2
            (3, 10, 24540.0),
            (4, 10, 24480.0),  # BE region in minute 4 (runner stops at BE)
        ],
    )
    outcomes = monitor.check(_now() + timedelta(minutes=6))
    assert len(outcomes) == 1
    oc = outcomes[0]
    assert oc.outcome == OUTCOME_TP1_BE
    assert oc.resolution_tf == "1m"
    assert oc.mfe_pct > 0
    assert oc.mae_pct >= 0


def test_5m_fallback_when_1m_coverage_missing() -> None:
    """Seeded 5m only (restart shape): the walk must fall back to 5m."""
    store = IndiaTickStore()
    monitor = IndiaTradeMonitor(store)
    sig = _level_signal("LONG")
    sig.entry_type = EntryType.MARKET
    monitor.register([sig], _now())
    _seed_5m(store, [(5, 24610.0, 24505.0, 24605.0)])
    monitor.check(_now() + timedelta(minutes=10))
    tracked = list(monitor._open.values())[0]
    assert tracked.resolution_tf == "5m"


def test_config_5m_resolution_ignores_1m(monkeypatch) -> None:
    monkeypatch.setattr(config, "OUTCOME_RESOLUTION_TF", "5m")
    store = IndiaTickStore()
    monitor = IndiaTradeMonitor(store)
    sig = _level_signal("LONG")
    sig.entry_type = EntryType.MARKET
    monitor.register([sig], _now())
    _ticks(store, [(0, 30, 24510.0), (1, 10, 24605.0), (2, 10, 24450.0)])
    outcomes = monitor.check(_now() + timedelta(minutes=6))
    # 5m walk: the (still-building) bucket spans TP1 and SL → conservative SL.
    assert outcomes[0].outcome == OUTCOME_SL
    assert outcomes[0].resolution_tf == "5m"
    assert outcomes[0].ambiguous_tie is True
