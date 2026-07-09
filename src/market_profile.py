"""NSE intraday volume profile — time-of-day normalisation for volume ratios.

NSE volume is strongly U-shaped across the session: the first half hour and
the close print multiples of the midday rate, with a pronounced lunch lull
(~12:00–13:00 IST). A raw "last bar ÷ 20-bar average" ratio therefore reads
ordinary opening turnover as a surge and misses a genuine midday surge whose
absolute volume is modest — the systematic bias flagged in Session 11.

Two corrections, both applied by ``tod_adjusted_volume_ratio``:

1. **Time-of-day factor** — every bar's volume is divided by the expected
   relative volume of its session bucket before the ratio is formed, so 1.0
   always means "normal for this time of day".
2. **Building-bar pro-rating** — the newest 5m bar is still forming when the
   scanner runs (30s cadence). Comparing 30s of accumulated volume against
   full-bar averages suppressed every early breakout and then re-detected it
   stale at bar close. The forming bar's volume is scaled up by the elapsed
   fraction of its bucket (floored so one large opening tick cannot fabricate
   a 10× surge).

The bucket table is a first calibration from the public NSE intraday turnover
shape; it is deliberately coarse (step function, 9 buckets) so it fails soft.
Set ``INDIA_VOL_TOD_ENABLE=false`` to fall back to pure pro-rated ratios.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import time

import config
from src.market.candle import Candle

VOL_TOD_ENABLE: bool = config._safe_bool("INDIA_VOL_TOD_ENABLE", True)

# A forming bar younger than this fraction of its bucket is treated as this
# old when pro-rating — caps the scale-up at 1/MIN_BAR_FRACTION (~3.3x).
MIN_BAR_FRACTION: float = config._safe_float("INDIA_VOL_MIN_BAR_FRACTION", 0.3)

# (bucket start IST, expected volume vs full-day per-bar mean). A time maps to
# the latest bucket whose start it has passed; pre-open maps to the first.
_TOD_BUCKETS: tuple[tuple[time, float], ...] = (
    (time(9, 15), 2.2),
    (time(9, 30), 1.5),
    (time(10, 0), 1.0),
    (time(11, 0), 0.8),
    (time(12, 0), 0.6),
    (time(13, 0), 0.75),
    (time(14, 0), 1.0),
    (time(14, 45), 1.3),
    (time(15, 10), 1.8),
)


def tod_factor(t: time) -> float:
    """Expected relative volume for a bar starting at IST time ``t``."""
    if not VOL_TOD_ENABLE:
        return 1.0
    naive = t.replace(tzinfo=None)
    factor = _TOD_BUCKETS[0][1]
    for start, f in _TOD_BUCKETS:
        if naive >= start:
            factor = f
        else:
            break
    return factor


def _seconds_of_day(t: time) -> int:
    return t.hour * 3600 + t.minute * 60 + t.second


def tod_adjusted_volume_ratio(
    candles_5m: Sequence[Candle],
    scan_time: time | None = None,
    period: int = 20,
    bar_seconds: int = 300,
) -> float:
    """Newest-bar volume vs the recent average, both TOD-normalised.

    The newest bar is pro-rated by its elapsed fraction when ``scan_time``
    falls inside its bucket. Returns 0.0 when there is not enough data —
    callers fall back to the raw ratio (``IndiaContext.current_volume_ratio``).
    """
    if len(candles_5m) < 2:
        return 0.0
    cur = candles_5m[-1]
    prior = candles_5m[-(period + 1) : -1]
    expected_terms = [b.volume / tod_factor(b.ts.timetz()) for b in prior]
    expected = sum(expected_terms) / len(expected_terms)
    if expected <= 0:
        return 0.0

    fraction = 1.0
    if scan_time is not None:
        elapsed = _seconds_of_day(scan_time) - _seconds_of_day(cur.ts.timetz())
        if 0 <= elapsed < bar_seconds:
            fraction = max(MIN_BAR_FRACTION, elapsed / bar_seconds)

    cur_adj = cur.volume / fraction / tod_factor(cur.ts.timetz())
    return cur_adj / expected
