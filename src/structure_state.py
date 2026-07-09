"""Market-structure detection: swing points, and BOS / CHoCH events.

The SMC evaluators need swing highs/lows (as liquidity levels and TP targets)
and the current break-of-structure character. A swing is a fractal pivot: a bar
whose high (low) exceeds (undercuts) the ``width`` bars on each side.

BOS (Break Of Structure) is a continuation break; CHoCH (Change of CHaracter)
is a break against the prevailing swing sequence — the first sign of reversal.
The trend read is intentionally simple (last two confirmed highs and lows) and
tunable; it is a first implementation, not a final SMC model.

``detect_structure`` classifies only the latest close. ``last_structure_event``
is the persistent form the scorer consumes: the most recent BOS/CHoCH within a
recent-bar window, each bar judged against only the swings that were confirmed
at that bar (no lookahead) — so an aligned break a few bars back still counts
as live structure bias instead of vanishing on the very next scan.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from src.market.candle import Candle


@dataclass(frozen=True)
class Swing:
    index: int
    price: float
    is_high: bool


class StructureEvent(StrEnum):
    BOS_UP = "BOS_UP"
    BOS_DOWN = "BOS_DOWN"
    CHOCH_UP = "CHoCH_UP"
    CHOCH_DOWN = "CHoCH_DOWN"


def find_swings(candles: Sequence[Candle], width: int = 2) -> list[Swing]:
    """All fractal swing highs/lows with ``width`` confirming bars each side.

    Comparison is strict against the bars *before* the pivot and >= against the
    bars *after* it, so an exact double-top/bottom (equal highs are routine at
    NSE round numbers) still registers one swing at its first bar instead of
    disappearing entirely — a fully strict fractal is blind to the very
    liquidity levels the sweep evaluators trade.
    """
    if width < 1:
        raise ValueError("find_swings: width must be >= 1")
    swings: list[Swing] = []
    for i in range(width, len(candles) - width):
        high = candles[i].high
        low = candles[i].low
        if all(
            high > candles[i - j].high and high >= candles[i + j].high
            for j in range(1, width + 1)
        ):
            swings.append(Swing(i, high, True))
        if all(
            low < candles[i - j].low and low <= candles[i + j].low
            for j in range(1, width + 1)
        ):
            swings.append(Swing(i, low, False))
    return swings


def last_swing_high(
    candles: Sequence[Candle], lookback: int = 20, width: int = 2
) -> float | None:
    """Price of the most recent swing high within the last ``lookback`` bars."""
    cutoff = len(candles) - lookback
    highs = [s for s in find_swings(candles, width) if s.is_high and s.index >= cutoff]
    return highs[-1].price if highs else None


def last_swing_low(
    candles: Sequence[Candle], lookback: int = 20, width: int = 2
) -> float | None:
    """Price of the most recent swing low within the last ``lookback`` bars."""
    cutoff = len(candles) - lookback
    lows = [s for s in find_swings(candles, width) if not s.is_high and s.index >= cutoff]
    return lows[-1].price if lows else None


def detect_structure(
    candles: Sequence[Candle], width: int = 2
) -> StructureEvent | None:
    """Classify the latest close as a BOS/CHoCH break, or ``None`` if neither."""
    swings = find_swings(candles, width)
    highs = [s for s in swings if s.is_high]
    lows = [s for s in swings if not s.is_high]
    if len(highs) < 2 or len(lows) < 2:
        return None
    last_close = candles[-1].close
    trend_up = highs[-1].price > highs[-2].price and lows[-1].price > lows[-2].price
    trend_down = highs[-1].price < highs[-2].price and lows[-1].price < lows[-2].price
    if last_close > highs[-1].price:
        return StructureEvent.CHOCH_UP if trend_down else StructureEvent.BOS_UP
    if last_close < lows[-1].price:
        return StructureEvent.CHOCH_DOWN if trend_up else StructureEvent.BOS_DOWN
    return None


def last_structure_event(
    candles: Sequence[Candle], width: int = 2, lookback: int = 12
) -> StructureEvent | None:
    """Most recent BOS/CHoCH within the last ``lookback`` bars, or ``None``.

    Walks backwards from the newest bar; each bar's close is judged only
    against swings already confirmed at that bar (``swing.index <= i - width``),
    so a break is attributed to the bar where it actually happened. The newest
    matching bar wins — that is the market's current structure state.
    """
    swings = find_swings(candles, width)
    if not swings:
        return None
    n = len(candles)
    for i in range(n - 1, max(width, n - lookback) - 1, -1):
        highs = [s for s in swings if s.is_high and s.index <= i - width]
        lows = [s for s in swings if not s.is_high and s.index <= i - width]
        if len(highs) < 2 or len(lows) < 2:
            continue
        close = candles[i].close
        trend_up = highs[-1].price > highs[-2].price and lows[-1].price > lows[-2].price
        trend_down = highs[-1].price < highs[-2].price and lows[-1].price < lows[-2].price
        if close > highs[-1].price:
            return StructureEvent.CHOCH_UP if trend_down else StructureEvent.BOS_UP
        if close < lows[-1].price:
            return StructureEvent.CHOCH_DOWN if trend_up else StructureEvent.BOS_DOWN
    return None
