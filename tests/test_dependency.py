"""Dependency pairs — sector groups, proxy chain, index bias (src/dependency.py)."""

from __future__ import annotations

import config
from src.dependency import (
    NEUTRAL,
    SECTOR_GROUPS,
    group_for,
    market_bias,
    proxy_candidates,
)
from src.signals.model import Direction
from tests.candle_factory import from_closes
from tests.signal_factory import make_context


def test_every_default_base_is_grouped() -> None:
    mapped = {b for members in SECTOR_GROUPS.values() for b in members}
    for base in (*config.INDEX_BASES, *config.STOCK_BASES):
        assert base in mapped, f"{base} missing from SECTOR_GROUPS"


def test_group_membership() -> None:
    assert group_for("HDFCBANK") == "BANKS"
    assert group_for("ICICIBANK") == "BANKS"
    assert group_for("NIFTY") == "INDEX"
    assert group_for("BANKNIFTY") == "INDEX"
    # Unknown base is its own group — never collides.
    assert group_for("SOMENEWSTOCK") == "SOMENEWSTOCK"


def test_proxy_chain() -> None:
    assert proxy_candidates("HDFCBANK")[0] == "BANKNIFTY"
    assert proxy_candidates("BAJFINANCE")[0] == "FINNIFTY"
    assert proxy_candidates("RELIANCE") == ("NIFTY",)
    # Indices cross-anchor each other.
    assert proxy_candidates("NIFTY") == ("BANKNIFTY",)
    assert proxy_candidates("BANKNIFTY") == ("NIFTY",)
    # Banks fall back to NIFTY when BANKNIFTY has no context.
    assert proxy_candidates("HDFCBANK")[-1] == "NIFTY"


def test_market_bias_long_when_up_day_above_ema() -> None:
    closes = [24000.0 + i * 5 for i in range(30)]  # steady climb
    ctx = make_context(candles_5m=from_closes(closes), day_open=24000.0)
    assert market_bias(ctx) == Direction.LONG


def test_market_bias_short_when_down_day_below_ema() -> None:
    closes = [24000.0 - i * 5 for i in range(30)]
    ctx = make_context(candles_5m=from_closes(closes), day_open=24000.0)
    assert market_bias(ctx) == Direction.SHORT


def test_market_bias_neutral_on_flat_day() -> None:
    closes = [24000.0 + (1 if i % 2 else -1) for i in range(30)]
    ctx = make_context(candles_5m=from_closes(closes), day_open=24000.0)
    assert market_bias(ctx) == NEUTRAL


def test_market_bias_neutral_without_day_open_or_history() -> None:
    ctx = make_context(day_open=0.0)
    assert market_bias(ctx) == NEUTRAL
    short_ctx = make_context(
        candles_5m=from_closes([24000.0] * 5), day_open=24000.0
    )
    assert market_bias(short_ctx) == NEUTRAL
