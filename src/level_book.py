"""Support/resistance Level Book.

Aggregates the S/R levels the evaluators reference into one sorted, clustered
book: fractal swing highs/lows, previous-day high/low/close, and round-number
levels (NIFTY every 50, BANKNIFTY every 100 — see the SR_FLIP evaluator).
Nearby levels are merged so a cluster of touches reads as one strong level
rather than several weak ones; a level's ``strength`` is the merged touch count.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from src.market.candle import Candle
from src.structure_state import find_swings


@dataclass(frozen=True)
class Level:
    price: float
    kind: str
    strength: int


def _round_levels(candles: Sequence[Candle], step: float) -> list[tuple[float, str]]:
    if step <= 0:
        raise ValueError("round step must be > 0")
    lo = min(c.low for c in candles)
    hi = max(c.high for c in candles)
    first = step * (int(lo / step) + 1)
    out: list[tuple[float, str]] = []
    level = first
    while level < hi:
        out.append((level, "round"))
        level += step
    return out


def _cluster(raw: list[tuple[float, str]], merge_tol: float) -> list[Level]:
    if not raw:
        return []
    raw_sorted = sorted(raw, key=lambda x: x[0])
    clusters: list[list[tuple[float, str]]] = [[raw_sorted[0]]]
    for price, kind in raw_sorted[1:]:
        if price - clusters[-1][-1][0] <= merge_tol:
            clusters[-1].append((price, kind))
        else:
            clusters.append([(price, kind)])
    levels: list[Level] = []
    for group in clusters:
        avg = sum(p for p, _ in group) / len(group)
        # Prefer a structural label over a plain "round" one for the merged level.
        kind = next((k for _, k in group if k != "round"), group[0][1])
        levels.append(Level(price=avg, kind=kind, strength=len(group)))
    return levels


class LevelBook:
    """A sorted, clustered set of S/R levels with nearest-level queries."""

    def __init__(self, levels: Sequence[Level]) -> None:
        self._levels = sorted(levels, key=lambda level: level.price)

    @classmethod
    def build(
        cls,
        candles: Sequence[Candle],
        *,
        round_step: float | None = None,
        extra: Iterable[tuple[float, str]] = (),
        swing_width: int = 2,
        merge_tol: float = 0.0,
    ) -> LevelBook:
        raw: list[tuple[float, str]] = []
        for s in find_swings(candles, swing_width):
            raw.append((s.price, "swing_high" if s.is_high else "swing_low"))
        raw.extend(extra)
        if round_step is not None:
            raw.extend(_round_levels(candles, round_step))
        return cls(_cluster(raw, merge_tol))

    def levels(self) -> list[Level]:
        return list(self._levels)

    def nearest_support(self, price: float) -> Level | None:
        """Highest level strictly below ``price``."""
        below = [level for level in self._levels if level.price < price]
        return below[-1] if below else None

    def nearest_resistance(self, price: float) -> Level | None:
        """Lowest level strictly above ``price``."""
        above = [level for level in self._levels if level.price > price]
        return above[0] if above else None
