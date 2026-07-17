"""Replay harness parity — tools/replay.resolve must match the live monitor.

The harness's whole authority rests on sharing walk_signal + the blend rules
with production; these tests pin that parity on both trigger modes.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import config
from src.data.india_tick_store import IndiaTickStore
from src.market.candle import Candle
from src.signals.model import EntryType, IndiaSignal
from src.trade_monitor import IndiaTradeMonitor
from tools.replay import ReplayRow, resolve

_SYM = "NSE:NIFTY26JULFUT"


def _now() -> datetime:
    return config.IST.localize(datetime(2026, 7, 6, 10, 0))


def _candles(bars: list[tuple[int, float, float, float]]) -> list[Candle]:
    base_ts = _now()
    return [
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


def _signal(entry_type: str) -> IndiaSignal:
    return IndiaSignal(
        signal_id="parity",
        symbol=_SYM,
        base="NIFTY",
        direction="LONG",
        setup_class="VOLUME_SURGE_BREAKOUT",
        entry=24500.0,
        sl=24450.0,
        tp1=24600.0,
        sl_pct=0.2,
        tp1_pct=0.4,
        rr_ratio=2.0,
        lot_size=65,
        tp2=24700.0,
        entry_type=entry_type,
    )


def _row(entry_type: str) -> ReplayRow:
    return ReplayRow(
        signal_id="parity",
        symbol=_SYM,
        base="NIFTY",
        setup_class="VOLUME_SURGE_BREAKOUT",
        direction="LONG",
        tier="B",
        entry=24500.0,
        sl=24450.0,
        tp1=24600.0,
        tp2=24700.0,
        entry_type=entry_type,
        created_at=_now(),
        stored_outcome="",
        stored_pct=0.0,
    )


def _monitor_outcome(
    entry_type: str, bars: list[tuple[int, float, float, float]]
) -> tuple[str, float]:
    store = IndiaTickStore()
    monitor = IndiaTradeMonitor(store)
    monitor.register([_signal(entry_type)], _now())
    store.seed(_SYM, _candles(bars))
    outcomes = monitor.check(_now() + timedelta(hours=6))
    if outcomes:
        return outcomes[0].outcome, round(outcomes[0].pct, 6)
    forced = monitor.force_close_all(_now() + timedelta(hours=6))
    return forced[0].outcome, round(forced[0].pct, 6)


def _replay_outcome(
    entry_type: str,
    bars: list[tuple[int, float, float, float]],
    entry_trigger: bool,
) -> tuple[str, float]:
    outcome, pct = resolve(
        _row(entry_type), _candles(bars), entry_trigger=entry_trigger
    )
    return outcome, round(pct, 6)


_SCENARIOS: list[list[tuple[int, float, float, float]]] = [
    # touch entry then TP1 then TP2
    [(5, 24520.0, 24495.0, 24510.0), (10, 24605.0, 24505.0, 24600.0),
     (15, 24705.0, 24600.0, 24700.0)],
    # straight to SL (spans entry)
    [(5, 24520.0, 24440.0, 24460.0)],
    # runs to TP1 without touching entry
    [(5, 24610.0, 24505.0, 24605.0)],
    # quiet expiry
    [(5, 24560.0, 24505.0, 24550.0), (10, 24565.0, 24510.0, 24555.0)],
    # TP1 then BE stop
    [(5, 24520.0, 24495.0, 24510.0), (10, 24605.0, 24505.0, 24600.0),
     (15, 24560.0, 24480.0, 24500.0)],
]


def test_parity_trigger_on_level_signals() -> None:
    for bars in _SCENARIOS:
        assert _replay_outcome(EntryType.LEVEL, bars, True) == _monitor_outcome(
            EntryType.LEVEL, bars
        ), f"trigger-on parity broke for {bars}"


def test_parity_legacy_market_signals(monkeypatch) -> None:
    for bars in _SCENARIOS:
        monkeypatch.setattr(config, "ENTRY_TRIGGER_ENABLED", True)
        live = _monitor_outcome(EntryType.MARKET, bars)
        replayed = _replay_outcome(EntryType.MARKET, bars, True)
        assert replayed == live, f"market parity broke for {bars}"


def test_parity_trigger_off_reproduces_legacy(monkeypatch) -> None:
    """--entry-trigger off must reproduce the pre-Session-21 semantics even
    for LEVEL signals (the fidelity mode used to validate the harness)."""
    for bars in _SCENARIOS:
        monkeypatch.setattr(config, "ENTRY_TRIGGER_ENABLED", False)
        live = _monitor_outcome(EntryType.LEVEL, bars)
        monkeypatch.setattr(config, "ENTRY_TRIGGER_ENABLED", True)
        replayed = _replay_outcome(EntryType.LEVEL, bars, False)
        assert replayed == live, f"fidelity parity broke for {bars}"
