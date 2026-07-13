"""Once-daily macro inputs — prev-day FII/DII net cash flows.

`INDIA_MARKET_DOCTRINE` §2: institutional flow (FII/DII) is the NSE analogue of
crypto's dominance/rotation — the strongest read on the day's directional bias.
This store holds the prev-day FII/DII net (₹ crore), fetched once at session
open (never on the tick/scan hot path — IB18) and freshness-gated like VIX: a
stale or never-fetched value reads as *unavailable* (0.0), which the market
direction classifier treats as NEUTRAL. **Never fabricated** — if the source is
unset or unreachable the vote simply doesn't fire.

Gift-Nifty is deliberately not fetched: the Fyers feed carries no GIFT/SGX
symbol, and for a post-09:30 engine the overnight-gap signal is already realised
in `day_open` vs `prev_day_close` (the opening gap the market_context computes
directly). So the only genuinely new external input here is FII/DII.

Source contract: `INDIA_FII_DII_URL` must return JSON carrying the prev-day net
figures. Parsing is tolerant of a few common key spellings; anything else →
unavailable. Point it at NSE's FII/DII report (or a thin adapter) at deploy.
"""

from __future__ import annotations

import time
from typing import Any

import config
from src.utils import get_logger

logger = get_logger("india_macro")

# Keys we accept for the FII / DII net cash figures (₹ crore), most-specific
# first. A source that uses none of these reads as unavailable.
_FII_KEYS = ("fii_net_cr", "fiiNetCr", "fii_net", "FII_net", "fiiDiiNetFII")
_DII_KEYS = ("dii_net_cr", "diiNetCr", "dii_net", "DII_net", "fiiDiiNetDII")


def _first_num(payload: dict, keys: tuple[str, ...]) -> float | None:
    for k in keys:
        if k in payload:
            try:
                return float(payload[k])
            except (TypeError, ValueError):
                return None
    return None


class IndiaMacroStore:
    """Prev-day FII/DII net cash, refreshed once per session open."""

    def __init__(self, ttl_sec: int | None = None) -> None:
        self._fii_cr = 0.0
        self._dii_cr = 0.0
        self._as_of: str = ""
        self._set_at: float = 0.0
        self._ttl = config.MACRO_TTL_SEC if ttl_sec is None else ttl_sec

    def set_fii_dii(self, fii_cr: float, dii_cr: float, as_of: str = "") -> None:
        self._fii_cr = fii_cr
        self._dii_cr = dii_cr
        self._as_of = as_of
        self._set_at = time.monotonic()

    def _fresh(self) -> bool:
        return self._set_at > 0.0 and (time.monotonic() - self._set_at) <= self._ttl

    def get_net_cr(self) -> float:
        """Combined institutional net (FII+DII) in ₹ crore, or 0.0 when
        unavailable/stale (→ NEUTRAL, never a fabricated flow)."""
        return self._fii_cr + self._dii_cr if self._fresh() else 0.0

    def snapshot(self) -> dict[str, Any]:
        """For observability (pulse/ops) — the raw figures + freshness."""
        return {
            "fii_net_cr": self._fii_cr if self._fresh() else None,
            "dii_net_cr": self._dii_cr if self._fresh() else None,
            "net_cr": self.get_net_cr(),
            "as_of": self._as_of,
            "available": self._fresh(),
        }

    async def refresh(self, http_client: Any | None = None) -> bool:
        """Fetch prev-day FII/DII from ``INDIA_FII_DII_URL`` once. No URL → skip
        (stays NEUTRAL). Any error → left unavailable, logged, never raised into
        the session loop. Returns True only on a successful set."""
        url = config.FII_DII_URL
        if not url:
            return False
        own_client = http_client is None
        try:
            import httpx

            client = http_client or httpx.AsyncClient(timeout=httpx.Timeout(10.0))
            try:
                resp = await client.get(url)
                resp.raise_for_status()
                payload = resp.json()
            finally:
                if own_client:
                    await client.aclose()
        except Exception as exc:  # network/parse — degrade to unavailable
            logger.warning("FII/DII fetch failed ({}): {}", url, exc)
            return False

        if not isinstance(payload, dict):
            logger.warning("FII/DII payload not an object — treating as unavailable")
            return False
        fii = _first_num(payload, _FII_KEYS)
        dii = _first_num(payload, _DII_KEYS)
        if fii is None and dii is None:
            logger.warning("FII/DII payload had no recognised keys — unavailable")
            return False
        as_of = str(payload.get("date") or payload.get("as_of") or "")
        self.set_fii_dii(fii or 0.0, dii or 0.0, as_of)
        logger.info(
            "FII/DII set: FII {:+.0f}cr DII {:+.0f}cr (as_of {})",
            self._fii_cr, self._dii_cr, as_of or "?",
        )
        return True
