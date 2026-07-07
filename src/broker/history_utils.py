"""Shared helpers for turning raw historical candles into context inputs."""

from __future__ import annotations

from datetime import date, datetime

from src.market.candle import Candle


def _bucket_start(ts: datetime, tf_minutes: int) -> datetime:
    """Truncate *ts* to the start of its clock-aligned timeframe bucket."""
    total = ts.hour * 60 + ts.minute
    start = (total // tf_minutes) * tf_minutes
    h, m = divmod(start, 60)
    return ts.replace(hour=h, minute=m, second=0, microsecond=0)


def aggregate_candles(candles_5m: list[Candle], tf_minutes: int) -> list[Candle]:
    """Aggregate a 5m candle series into a higher timeframe (15m / 60m).

    Buckets by *clock time* per calendar day — the same bucketing the tick
    store uses for live bars — so seeded and live higher-timeframe candles
    share boundaries. (The previous fixed-count grouping drifted across the
    NSE session's 75 5m bars and could merge bars across the overnight gap,
    producing 60m candles that mixed two trading days.) Used so the 60m
    regime (EMA21/EMA55 -> needs ~56 bars) can form at session open instead
    of waiting ~9 trading days to accrue live.
    """
    if tf_minutes <= 0 or not candles_5m:
        return list(candles_5m)
    result: list[Candle] = []
    group: list[Candle] = []
    group_key: tuple[date, datetime] | None = None
    for c in candles_5m:
        key = (c.ts.date(), _bucket_start(c.ts, tf_minutes))
        if group_key is None or key == group_key:
            group.append(c)
            group_key = key
            continue
        result.append(_merge(group, group_key[1]))
        group = [c]
        group_key = key
    if group and group_key is not None:
        result.append(_merge(group, group_key[1]))
    return result


def _merge(group: list[Candle], bucket_ts: datetime) -> Candle:
    return Candle(
        ts=bucket_ts,
        open=group[0].open,
        high=max(c.high for c in group),
        low=min(c.low for c in group),
        close=group[-1].close,
        volume=sum(c.volume for c in group),
    )


class CumulativeVolume:
    """Turns a broker's cumulative day-volume field into per-tick deltas.

    Both Fyers (``vol_traded_today``) and Angel One
    (``volume_trade_for_the_day``) report *cumulative* volume since the day's
    open on every tick. Feeding that raw number into the candle builder sums
    the running total once per tick, inflating live-bar volume by orders of
    magnitude — every volume gate then passes trivially and volume scoring
    maxes out. This tracker keeps the last cumulative reading per symbol and
    returns only the increment.

    Rules:
      * first observation of a symbol → 0 (baseline; the day's earlier volume
        already lives in the seeded candles)
      * new IST date → the reading itself (it IS today's volume so far)
      * decreased reading on the same day (feed reset) → 0, re-baseline
    """

    def __init__(self) -> None:
        self._last: dict[str, tuple[date, float]] = {}

    def delta(self, symbol: str, cumulative: float, ts: datetime) -> float:
        day = ts.date()
        prev = self._last.get(symbol)
        self._last[symbol] = (day, cumulative)
        if prev is None:
            return 0.0
        prev_day, prev_cum = prev
        if day != prev_day:
            return cumulative
        if cumulative < prev_cum:
            return 0.0
        return cumulative - prev_cum

    def reset(self) -> None:
        self._last.clear()


def prev_session_levels(
    candles: list[Candle], today: date
) -> tuple[float, float, float] | None:
    """(high, low, close) of the most recent session strictly before *today*.

    The seed window spans the previous trading session plus today; candles
    are bucketed by calendar date and the latest pre-today date wins (so a
    Monday correctly picks Friday, skipping the weekend gap).
    """
    prior = [c for c in candles if c.ts.date() < today]
    if not prior:
        return None
    last_session = max(c.ts.date() for c in prior)
    session = [c for c in prior if c.ts.date() == last_session]
    return (
        max(c.high for c in session),
        min(c.low for c in session),
        session[-1].close,
    )
