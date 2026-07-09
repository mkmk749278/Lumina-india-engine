"""Dependency pairs — cross-instrument structure for the 46-base universe.

NSE F&O names do not move independently: bank stocks are ~0.8+ correlated with
BANKNIFTY, most large caps with NIFTY, and NIFTY/BANKNIFTY with each other.
Before this module every signal was evaluated in isolation — a stock LONG
scored identically whether its index was rallying or crashing, and one
correlated index move could emit several near-identical same-direction stock
signals in a single scan (the per-scan cap kept the *count* down but not the
concentration).

Three primitives, consumed by the scanner and the scorer:

* ``group_for(base)`` — the correlation group a base belongs to. The emission
  stage caps same-direction signals per group per scan, so subscribers get the
  single best expression of a sector move, not three copies of it.
* ``proxy_candidates(base)`` — the index (in preference order) whose intraday
  bias anchors this base. Banks → BANKNIFTY, NBFCs → FINNIFTY, everything else
  → NIFTY; the indices cross-anchor each other (NIFTY ↔ BANKNIFTY).
* ``market_bias(ctx)`` — the proxy's intraday bias: day-change beyond a
  minimum threshold *and* price on the matching side of the 5m EMA21. Both
  must agree or the bias is NEUTRAL — a flat index anchors nothing.

Groups are static config, not fitted correlations: they encode NSE sector
membership, which is stable, auditable, and has no look-ahead risk.
"""

from __future__ import annotations

import config
from src.indicators import ema
from src.signals.model import Direction, IndiaContext

# Minimum |day change| (%) of the proxy index before it expresses a bias.
INDEX_BIAS_MIN_PCT: float = config._safe_float("INDIA_INDEX_BIAS_MIN_PCT", 0.10)

NEUTRAL = "NEUTRAL"

# Correlation groups over the default universe (config.STOCK_BASES +
# config.INDEX_BASES). A base absent from every group is its own group —
# unknown/custom bases never collide with each other.
SECTOR_GROUPS: dict[str, tuple[str, ...]] = {
    "INDEX": ("NIFTY", "BANKNIFTY", "FINNIFTY", "NIFTYNXT50"),
    "BANKS": ("HDFCBANK", "ICICIBANK", "SBIN", "AXISBANK", "KOTAKBANK"),
    "NBFC": ("BAJFINANCE", "BAJAJFINSV"),
    "IT": ("INFY", "TCS", "HCLTECH", "WIPRO", "TECHM"),
    "METALS": ("TATASTEEL", "JSWSTEEL", "HINDALCO", "VEDL", "SAIL"),
    "AUTO": ("TATAMOTORS", "MARUTI", "EICHERMOT"),
    "PHARMA": ("SUNPHARMA", "DRREDDY", "CIPLA", "DIVISLAB"),
    "FMCG": ("ITC", "HINDUNILVR", "NESTLEIND", "BRITANNIA"),
    "ENERGY": ("RELIANCE", "ONGC", "COALINDIA", "POWERGRID", "NTPC"),
    "ADANI": ("ADANIENT", "ADANIPORTS"),
    "INFRA": ("LT", "ULTRACEMCO", "GRASIM", "DLF"),
    "CONSUMER": ("TITAN", "ASIANPAINT"),
    "TELECOM": ("BHARTIARTL",),
}

_GROUP_OF: dict[str, str] = {
    base: group for group, members in SECTOR_GROUPS.items() for base in members
}

# Sector groups whose natural anchor is not NIFTY.
_GROUP_PROXY: dict[str, tuple[str, ...]] = {
    "BANKS": ("BANKNIFTY", "NIFTY"),
    "NBFC": ("FINNIFTY", "NIFTY"),
}


def group_for(base: str) -> str:
    """Correlation-group name for *base* (its own name if unmapped)."""
    return _GROUP_OF.get(base.upper(), base.upper())


def proxy_candidates(base: str) -> tuple[str, ...]:
    """Anchor indices for *base*, in preference order (first available wins)."""
    b = base.upper()
    if b == "NIFTY":
        return ("BANKNIFTY",)
    if b in config.INDEX_BASES:
        return ("NIFTY",)
    return _GROUP_PROXY.get(group_for(b), ("NIFTY",))


def market_bias(ctx: IndiaContext) -> str:
    """Intraday directional bias of an index context.

    LONG when the day change is at least ``INDEX_BIAS_MIN_PCT`` up *and* price
    holds above the 5m EMA21 (mirror for SHORT); otherwise NEUTRAL. Requires a
    real ``day_open`` and enough 5m bars for the EMA to mean anything.
    """
    if ctx.day_open <= 0 or len(ctx.candles_5m) < 21:
        return NEUTRAL
    last = ctx.candles_5m[-1].close
    day_change_pct = (last - ctx.day_open) / ctx.day_open * 100.0
    ema21 = ema([c.close for c in ctx.candles_5m], 21)
    if day_change_pct >= INDEX_BIAS_MIN_PCT and last > ema21:
        return Direction.LONG
    if day_change_pct <= -INDEX_BIAS_MIN_PCT and last < ema21:
        return Direction.SHORT
    return NEUTRAL
