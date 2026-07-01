"""Order Blocks and Fair Value Gaps (SMC primitives).

The SMC evaluators reference recent *unmitigated* order blocks and FVGs as entry
zones and confluence. Both are simple, documented first implementations over the
typed candle list — tunable, not a final SMC model.

FVG (3-candle imbalance):
  bullish -> candle[i+1].low  > candle[i-1].high  (gap left below price)
  bearish -> candle[i+1].high < candle[i-1].low   (gap left above price)

Order Block (last opposing candle before a displacement):
  bullish -> a down candle immediately followed by an up candle that closes
             beyond the down candle's high; zone = that down candle's range.
  bearish -> mirror.

"Unmitigated" means later price has not yet traded back into the zone/gap.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from src.market.candle import Candle


@dataclass(frozen=True)
class Zone:
    """A price zone (order block or FVG). ``top`` >= ``bottom``."""

    index: int
    top: float
    bottom: float
    bullish: bool
    kind: str  # "order_block" | "fvg"


def find_fvgs(candles: Sequence[Candle], min_size: float = 0.0) -> list[Zone]:
    """All 3-candle fair value gaps at least ``min_size`` wide."""
    zones: list[Zone] = []
    for i in range(1, len(candles) - 1):
        prev_c, next_c = candles[i - 1], candles[i + 1]
        if next_c.low - prev_c.high > min_size:
            zones.append(Zone(i, next_c.low, prev_c.high, True, "fvg"))
        elif prev_c.low - next_c.high > min_size:
            zones.append(Zone(i, prev_c.low, next_c.high, False, "fvg"))
    return zones


def find_order_blocks(candles: Sequence[Candle]) -> list[Zone]:
    """Order blocks: last opposing candle before a displacement close-through."""
    zones: list[Zone] = []
    for i in range(len(candles) - 1):
        cur, nxt = candles[i], candles[i + 1]
        cur_down = cur.close < cur.open
        cur_up = cur.close > cur.open
        if cur_down and nxt.close > cur.high:
            zones.append(Zone(i, cur.high, cur.low, True, "order_block"))
        elif cur_up and nxt.close < cur.low:
            zones.append(Zone(i, cur.high, cur.low, False, "order_block"))
    return zones


def _is_mitigated(zone: Zone, candles: Sequence[Candle]) -> bool:
    """True once a bar after the zone trades back into it."""
    for c in candles[zone.index + 2 :]:
        if c.low <= zone.top and c.high >= zone.bottom:
            return True
    return False


def unmitigated(zones: Sequence[Zone], candles: Sequence[Candle]) -> list[Zone]:
    """Filter to zones later price has not yet traded back into."""
    return [z for z in zones if not _is_mitigated(z, candles)]
