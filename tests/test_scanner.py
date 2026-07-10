"""IndiaScanner + GateChain — scan loop, gate suppression, scoring."""

from __future__ import annotations

from datetime import datetime, timedelta

import config
from src.channels.base import Evaluator
from src.data.india_context_builder import IndiaContextBuilder
from src.data.india_market_data import IndiaMarketData
from src.data.india_oi_store import IndiaOIStore
from src.data.india_tick_store import IndiaTickStore
from src.market.candle import Candle
from src.scanner import (
    _MAX_PER_DAY,
    _MAX_PER_DIRECTION,
    _MAX_PER_SCAN,
    GateChain,
    IndiaScanner,
)
from src.session.expiry_manager import ExpiryManager
from src.session.session_manager import SessionManager, SessionState
from src.signals.model import Direction, IndiaContext, IndiaSignal, SetupClass, Tier
from tests.candle_factory import c
from tests.signal_factory import make_context, make_signal

IST = config.IST
_SYM = "NSE:NIFTY26JULFUT"
_BASE = "NIFTY"

_BASE_DT = IST.localize(datetime(2026, 7, 7, 0, 0, 0))


def _ist(h: int, m: int) -> datetime:
    return _BASE_DT + timedelta(hours=h, minutes=m)


# ── Gate chain tests ─────────────────────────────────────────────────


def test_session_gate_suppresses_when_closed() -> None:
    chain = GateChain()
    sig = make_signal()
    ctx = make_context()
    result = chain.check(sig, ctx, SessionState.CLOSED, _ist(8, 0))
    assert result == "session_gate"


def test_session_gate_passes_when_open() -> None:
    chain = GateChain()
    sig = make_signal()
    ctx = make_context()
    result = chain.check(sig, ctx, SessionState.OPEN, _ist(10, 0))
    assert result is None


def test_cooldown_gate_suppresses_recent_fire() -> None:
    chain = GateChain()
    sig = make_signal(setup_class=SetupClass.TREND_PULLBACK_EMA)
    ctx = make_context(base=_BASE)
    now = _ist(10, 0)

    chain.record_emission(sig.setup_class, _BASE, sig.direction, now)

    result = chain.check(
        sig, ctx, SessionState.OPEN, now + timedelta(seconds=60)
    )
    assert result == "cooldown_gate"


def test_cooldown_gate_passes_after_cooldown() -> None:
    chain = GateChain()
    sig = make_signal(setup_class=SetupClass.TREND_PULLBACK_EMA)
    ctx = make_context(base=_BASE)
    now = _ist(10, 0)

    chain.record_emission(sig.setup_class, _BASE, sig.direction, now)

    from src.scanner import _COOLDOWN_SEC

    result = chain.check(
        sig, ctx, SessionState.OPEN, now + timedelta(seconds=_COOLDOWN_SEC + 60)
    )
    assert result is None or result != "cooldown_gate"


def test_event_risk_gate_suppresses_high_vix() -> None:
    chain = GateChain()
    sig = make_signal()
    ctx = make_context(india_vix=26.0)
    result = chain.check(sig, ctx, SessionState.OPEN, _ist(10, 0))
    assert result == "event_risk_gate"


def test_event_risk_gate_passes_normal_vix() -> None:
    chain = GateChain()
    sig = make_signal()
    ctx = make_context(india_vix=15.0)
    result = chain.check(sig, ctx, SessionState.OPEN, _ist(10, 0))
    assert result is None


def test_circuit_check_gate_suppresses_extreme_move() -> None:
    chain = GateChain()
    sig = make_signal()
    candles = [c(high=25300.0, low=25200.0, close=25280.0)]
    ctx = make_context(candles_5m=candles, day_open=24000.0)
    result = chain.check(sig, ctx, SessionState.OPEN, _ist(10, 0))
    assert result == "circuit_check_gate"


def test_circuit_check_gate_passes_normal_move() -> None:
    chain = GateChain()
    sig = make_signal()
    candles = [c(high=24010.0, low=23990.0, close=24005.0)]
    ctx = make_context(candles_5m=candles, day_open=24000.0)
    result = chain.check(sig, ctx, SessionState.OPEN, _ist(10, 0))
    assert result is None


def test_min_atr_gate_suppresses_low_atr() -> None:
    chain = GateChain()
    sig = make_signal()
    ctx = make_context(atr14_5m=1.0)
    result = chain.check(sig, ctx, SessionState.OPEN, _ist(10, 0))
    assert result == "min_atr_gate"


def test_min_atr_gate_passes_normal_atr() -> None:
    chain = GateChain()
    sig = make_signal()
    ctx = make_context(atr14_5m=10.0)
    result = chain.check(sig, ctx, SessionState.OPEN, _ist(10, 0))
    assert result is None


def test_oi_liquidity_gate_suppresses_low_oi() -> None:
    chain = GateChain()
    sig = make_signal()
    ctx = make_context(atr14_5m=10.0)
    object.__setattr__(ctx, "current_oi", 50_000.0)
    result = chain.check(sig, ctx, SessionState.OPEN, _ist(10, 0))
    assert result == "oi_liquidity_gate"


def test_oi_liquidity_gate_passes_zero_oi() -> None:
    """Zero OI means data not yet available — don't suppress."""
    chain = GateChain()
    sig = make_signal()
    ctx = make_context(atr14_5m=10.0)
    result = chain.check(sig, ctx, SessionState.OPEN, _ist(10, 0))
    assert result is None


def test_duplicate_direction_gate_suppresses_at_cap() -> None:
    chain = GateChain()
    sig = make_signal(direction=Direction.LONG)
    ctx = make_context(base=_BASE, atr14_5m=10.0)
    now = _ist(10, 0)

    # Fill the per-(base, direction) daily cap; the next same-direction
    # candidate is then suppressed at the emission stage.
    for _ in range(_MAX_PER_DIRECTION):
        chain.record_emission(
            SetupClass.VOLUME_SURGE_BREAKOUT, _BASE, Direction.LONG, now
        )

    result = chain.check_emission(
        sig, ctx, now + timedelta(seconds=600), emitted_this_scan=0
    )
    assert result == "duplicate_direction_gate"


def test_duplicate_direction_gate_allows_below_cap() -> None:
    chain = GateChain()
    sig = make_signal(direction=Direction.LONG)
    ctx = make_context(base=_BASE, atr14_5m=10.0)
    now = _ist(10, 0)

    # One emission is below the default cap of 2 — a second same-direction
    # setup may still fire on a later scan (the correlation-group cap binds
    # only within one scan; begin_scan simulates the next cycle).
    chain.record_emission(SetupClass.OPENING_RANGE_BREAKOUT, _BASE, Direction.LONG, now)
    chain.begin_scan()

    result = chain.check_emission(
        sig, ctx, now + timedelta(seconds=600), emitted_this_scan=0
    )
    assert result is None


def test_duplicate_direction_gate_passes_different_direction() -> None:
    chain = GateChain()
    sig = make_signal(direction=Direction.SHORT)
    ctx = make_context(base=_BASE, atr14_5m=10.0)
    now = _ist(10, 0)

    chain.record_emission(SetupClass.VOLUME_SURGE_BREAKOUT, _BASE, Direction.LONG, now)
    chain.begin_scan()

    # An opposite-direction signal is fine once the conflict window has passed.
    result = chain.check_emission(
        sig, ctx, now + timedelta(minutes=45), emitted_this_scan=0
    )
    assert result is None


def test_direction_conflict_gate_blocks_opposite_within_window() -> None:
    chain = GateChain()
    now = _ist(10, 0)
    chain.record_emission(SetupClass.TREND_PULLBACK_EMA, _BASE, Direction.LONG, now)

    sig = make_signal(direction=Direction.SHORT, setup_class=SetupClass.BREAKDOWN_SHORT)
    ctx = make_context(base=_BASE, atr14_5m=10.0)
    result = chain.check_emission(
        sig, ctx, now + timedelta(minutes=10), emitted_this_scan=0
    )
    assert result == "direction_conflict_gate"


def test_correlation_group_gate_caps_same_direction_sector_per_scan() -> None:
    chain = GateChain()
    now = _ist(10, 0)
    # HDFCBANK LONG already emitted this scan; ICICIBANK LONG is the same
    # BANKS-group move — suppressed within the scan, allowed next scan.
    chain.record_emission(
        SetupClass.VOLUME_SURGE_BREAKOUT, "HDFCBANK", Direction.LONG, now
    )
    sig = make_signal(direction=Direction.LONG, base="ICICIBANK")
    ctx = make_context(base="ICICIBANK", atr14_5m=10.0)

    result = chain.check_emission(sig, ctx, now, emitted_this_scan=1)
    assert result == "correlation_group_gate"

    chain.begin_scan()
    result = chain.check_emission(sig, ctx, now + timedelta(seconds=30), emitted_this_scan=0)
    assert result is None


def test_scan_cap_gate_suppresses_overflow_within_one_scan() -> None:
    chain = GateChain()
    sig = make_signal(direction=Direction.LONG)
    ctx = make_context(base=_BASE, atr14_5m=10.0)
    result = chain.check_emission(
        sig, ctx, _ist(10, 0), emitted_this_scan=_MAX_PER_SCAN
    )
    assert result == "scan_cap_gate"


def test_daily_cap_gate_suppresses_when_cap_configured(monkeypatch) -> None:
    # The daily cap is OFF by default (0 = unlimited, owner decision) —
    # enable a small one to cover the gate itself.
    import src.scanner as scanner_mod

    monkeypatch.setattr(scanner_mod, "_MAX_PER_DAY", 5)
    chain = GateChain()
    sig = make_signal(direction=Direction.LONG)
    now = _ist(10, 0)
    # Spend the whole configured budget across many bases so the
    # per-direction cap never triggers first.
    for i in range(5):
        chain.record_emission(
            SetupClass.VOLUME_SURGE_BREAKOUT, f"STOCK{i}", Direction.LONG, now
        )
    ctx = make_context(base=_BASE, atr14_5m=10.0)
    result = chain.check_emission(sig, ctx, now, emitted_this_scan=0)
    assert result == "daily_cap_gate"


def test_daily_cap_disabled_by_default_is_unlimited() -> None:
    assert _MAX_PER_DAY == 0  # no fixed daily signal budget (owner decision)
    chain = GateChain()
    now = _ist(10, 0)
    for i in range(50):
        chain.record_emission(
            SetupClass.VOLUME_SURGE_BREAKOUT, f"STOCK{i}", Direction.LONG, now
        )
    chain.begin_scan()
    sig = make_signal(direction=Direction.LONG)
    ctx = make_context(base=_BASE, atr14_5m=10.0)
    result = chain.check_emission(sig, ctx, now, emitted_this_scan=0)
    assert result != "daily_cap_gate"


def test_confidence_floor_gate_suppresses_low_score() -> None:
    chain = GateChain()
    sig = make_signal()
    ctx = make_context(atr14_5m=10.0)
    below_floor = config.CONFIDENCE_EMIT_FLOOR - 5.0
    result = chain.check(
        sig, ctx, SessionState.OPEN, _ist(10, 0), confidence=below_floor
    )
    assert result == "confidence_floor_gate"


def test_confidence_floor_gate_passes_high_score() -> None:
    chain = GateChain()
    sig = make_signal()
    ctx = make_context(atr14_5m=10.0)
    result = chain.check(sig, ctx, SessionState.OPEN, _ist(10, 0), confidence=80.0)
    assert result is None


def test_suppressions_logged() -> None:
    chain = GateChain()
    sig = make_signal()
    ctx = make_context(india_vix=30.0)
    chain.check(sig, ctx, SessionState.OPEN, _ist(10, 0))
    assert len(chain.suppressions) == 1
    assert chain.suppressions[0].gate == "event_risk_gate"


def test_reset_day_clears_state() -> None:
    chain = GateChain()
    sig = make_signal()
    ctx = make_context(india_vix=30.0)
    chain.check(sig, ctx, SessionState.OPEN, _ist(10, 0))
    chain.record_emission(SetupClass.TREND_PULLBACK_EMA, _BASE, Direction.LONG, _ist(10, 0))

    chain.reset_day()

    assert len(chain.suppressions) == 0
    assert len(chain._emitted_today) == 0


# ── Scanner integration tests ────────────────────────────────────────

class _AlwaysLongEvaluator(Evaluator):
    setup_class = SetupClass.TREND_PULLBACK_EMA
    enabled = True

    def evaluate(self, ctx: IndiaContext) -> IndiaSignal | None:
        if not ctx.candles_5m:
            return None
        entry = ctx.candles_5m[-1].close
        sl = entry - 20.0
        tp1 = entry + 60.0
        return IndiaSignal(
            signal_id="test",
            symbol=ctx.symbol,
            base=ctx.base,
            direction=Direction.LONG,
            setup_class=self.setup_class,
            entry=entry,
            sl=sl,
            tp1=tp1,
            sl_pct=abs(entry - sl) / entry * 100,
            tp1_pct=abs(tp1 - entry) / entry * 100,
            rr_ratio=3.0,
            lot_size=75,
            htf_trend_aligned=True,
            breakout_volume_ratio=2.0,
        )


class _NeverFireEvaluator(Evaluator):
    setup_class = SetupClass.OPENING_RANGE_BREAKOUT
    enabled = True

    def evaluate(self, ctx: IndiaContext) -> IndiaSignal | None:
        return None


def _make_scanner(evaluators: list[Evaluator] | None = None):
    tick = IndiaTickStore()
    oi = IndiaOIStore()
    mkt = IndiaMarketData()
    expiry = ExpiryManager()

    candles = [
        Candle(
            ts=_ist(9, 15) + timedelta(minutes=i * 5),
            open=24000.0 + i * 10,
            high=24010.0 + i * 10,
            low=23990.0 + i * 10,
            close=24005.0 + i * 10,
            volume=1500.0,
        )
        for i in range(60)
    ]
    tick.seed(_SYM, candles)
    # Mark the symbol's data live (stale_data_gate suppresses seed-only
    # buffers); the timestamp covers every scan time these tests use.
    tick._last_tick_ts[_SYM] = _ist(11, 30)
    mkt.update_vix(15.0)

    builder = IndiaContextBuilder(tick, oi, mkt, expiry)
    builder.set_prev_day(_SYM, high=24200.0, low=23800.0, close=24000.0)

    session = SessionManager()
    return IndiaScanner(builder, session, expiry, evaluators=evaluators)


def test_scanner_emits_signal_during_market_hours(monkeypatch) -> None:
    # Scan-mechanics test: the fixture context scores in the low 50s, so pin
    # the emit floor out of the way (calibration is tested elsewhere).
    monkeypatch.setattr(config, "CONFIDENCE_EMIT_FLOOR", 0.0)
    scanner = _make_scanner([_AlwaysLongEvaluator()])
    now = _ist(11, 0)
    symbols = {_BASE: _SYM}

    signals = scanner.scan(symbols, now)

    assert len(signals) == 1
    assert signals[0].setup_class == SetupClass.TREND_PULLBACK_EMA
    assert signals[0].direction == Direction.LONG
    assert signals[0].confidence > 0
    assert signals[0].tier in (Tier.A_PLUS, Tier.B)


def test_scanner_suppresses_outside_market_hours() -> None:
    scanner = _make_scanner([_AlwaysLongEvaluator()])
    now = _ist(8, 0)
    signals = scanner.scan({_BASE: _SYM}, now)
    assert len(signals) == 0


def test_scanner_skips_disabled_evaluator() -> None:
    ev = _AlwaysLongEvaluator()
    ev.enabled = False
    scanner = _make_scanner([ev])
    signals = scanner.scan({_BASE: _SYM}, _ist(11, 0))
    assert len(signals) == 0


class _IndexOnlyEvaluator(_AlwaysLongEvaluator):
    setup_class = SetupClass.PCR_EXTREME
    index_only = True


def test_scanner_skips_index_only_evaluator_for_stock() -> None:
    """Index-only setups (PCR / gamma) must not run for stock bases, which have
    no market-wide PCR / max-pain. The general evaluators still fire."""
    stock_sym = "NSE:RELIANCE26JULFUT"
    tick = IndiaTickStore()
    oi = IndiaOIStore()
    mkt = IndiaMarketData()
    expiry = ExpiryManager()
    candles = [
        Candle(
            ts=_ist(9, 15) + timedelta(minutes=i * 5),
            open=1400.0 + i, high=1405.0 + i, low=1395.0 + i,
            close=1402.0 + i, volume=1500.0,
        )
        for i in range(60)
    ]
    tick.seed(stock_sym, candles)
    tick._last_tick_ts[stock_sym] = _ist(11, 30)  # live data for stale gate
    mkt.update_vix(15.0)
    builder = IndiaContextBuilder(tick, oi, mkt, expiry)
    builder.set_prev_day(stock_sym, high=1450.0, low=1380.0, close=1400.0)
    scanner = IndiaScanner(
        builder, SessionManager(), expiry,
        evaluators=[_IndexOnlyEvaluator(), _AlwaysLongEvaluator()],
    )

    assert "RELIANCE" in config.ALLOWED_BASES
    assert "RELIANCE" not in config.INDEX_BASES

    signals = scanner.scan({"RELIANCE": stock_sym}, _ist(11, 0))

    # Only the non-index-only evaluator fired; PCR_EXTREME was skipped.
    setups = {s.setup_class for s in signals}
    assert SetupClass.PCR_EXTREME not in setups
    assert SetupClass.TREND_PULLBACK_EMA in setups


def test_scanner_no_signals_from_never_fire() -> None:
    scanner = _make_scanner([_NeverFireEvaluator()])
    signals = scanner.scan({_BASE: _SYM}, _ist(11, 0))
    assert len(signals) == 0


def test_scanner_cooldown_prevents_second_scan(monkeypatch) -> None:
    monkeypatch.setattr(config, "CONFIDENCE_EMIT_FLOOR", 0.0)
    scanner = _make_scanner([_AlwaysLongEvaluator()])
    now = _ist(11, 0)

    first = scanner.scan({_BASE: _SYM}, now)
    assert len(first) == 1

    second = scanner.scan({_BASE: _SYM}, now + timedelta(seconds=30))
    assert len(second) == 0


def test_scanner_reset_day(monkeypatch) -> None:
    monkeypatch.setattr(config, "CONFIDENCE_EMIT_FLOOR", 0.0)
    scanner = _make_scanner([_AlwaysLongEvaluator()])
    now = _ist(11, 0)

    scanner.scan({_BASE: _SYM}, now)
    scanner.reset_day()

    signals = scanner.scan({_BASE: _SYM}, now + timedelta(seconds=600))
    assert len(signals) == 1


def test_scanner_stamps_metadata(monkeypatch) -> None:
    monkeypatch.setattr(config, "CONFIDENCE_EMIT_FLOOR", 0.0)
    scanner = _make_scanner([_AlwaysLongEvaluator()])
    signals = scanner.scan({_BASE: _SYM}, _ist(11, 0))
    sig = signals[0]
    assert sig.atr_at_entry > 0
    assert sig.vix_at_entry == 15.0
    assert sig.expiry_date is not None


class _CaptureBiasEvaluator(Evaluator):
    """Records the index_bias each context arrives with; never fires."""

    setup_class = SetupClass.OPENING_RANGE_BREAKOUT
    enabled = True

    def __init__(self) -> None:
        self.seen: dict[str, str] = {}

    def evaluate(self, ctx: IndiaContext) -> IndiaSignal | None:
        self.seen[ctx.base] = ctx.index_bias
        return None


def test_scanner_anchors_stock_context_to_index_bias() -> None:
    # NIFTY rallying intraday -> RELIANCE (proxy NIFTY) is stamped LONG bias.
    tick = IndiaTickStore()
    oi = IndiaOIStore()
    mkt = IndiaMarketData()
    expiry = ExpiryManager()

    stock_sym = "NSE:RELIANCE26JULFUT"
    nifty_candles = [
        Candle(
            ts=_ist(9, 15) + timedelta(minutes=i * 5),
            open=24000.0 + i * 10,
            high=24010.0 + i * 10,
            low=23990.0 + i * 10,
            close=24005.0 + i * 10,
            volume=1500.0,
        )
        for i in range(30)
    ]
    stock_candles = [
        Candle(
            ts=_ist(9, 15) + timedelta(minutes=i * 5),
            open=1500.0,
            high=1501.0,
            low=1499.0,
            close=1500.0,
            volume=900.0,
        )
        for i in range(30)
    ]
    tick.seed(_SYM, nifty_candles)
    tick.seed_intraday_state(_SYM, nifty_candles, _ist(11, 45))
    tick.seed(stock_sym, stock_candles)
    mkt.update_vix(15.0)

    builder = IndiaContextBuilder(tick, oi, mkt, expiry)
    capture = _CaptureBiasEvaluator()
    scanner = IndiaScanner(builder, SessionManager(), expiry, evaluators=[capture])

    scanner.scan({_BASE: _SYM, "RELIANCE": stock_sym}, _ist(11, 45))

    assert capture.seen["RELIANCE"] == Direction.LONG
    # NIFTY itself anchors to BANKNIFTY, which has no context here -> NEUTRAL.
    assert capture.seen[_BASE] == "NEUTRAL"
