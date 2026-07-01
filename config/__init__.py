"""Central configuration for the Lumin India engine.

Every value is overridable via an environment variable so one image runs
identically in dev, CI, and on the VPS with only env changes (OWNER_BRIEF IB8).
Mirrors the crypto engine's config pattern.

Scope discipline (CLAUDE.md — no scaffolds): this module holds only settings
that shipped code actually consumes. Evaluator thresholds, scoring weights, and
blast-radius caps land alongside the modules that read them — not here as
unused constants.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import time
from pathlib import Path

import pytz

# --- env helpers ---------------------------------------------------------


def _safe_int(key: str, default: int) -> int:
    try:
        return int(os.environ[key])
    except (KeyError, ValueError, TypeError):
        return default


def _safe_float(key: str, default: float) -> float:
    try:
        return float(os.environ[key])
    except (KeyError, ValueError, TypeError):
        return default


def _safe_bool(key: str, default: bool) -> bool:
    raw = os.environ.get(key)
    if raw is None:
        return default
    v = raw.strip().lower()
    if v in ("true", "1", "yes", "on"):
        return True
    if v in ("false", "0", "no", "off"):
        return False
    return default


def _safe_str(key: str, default: str) -> str:
    return os.environ.get(key, default)


def _safe_time(key: str, default: time) -> time:
    """Parse an ``HH:MM`` env value, falling back to ``default``."""
    raw = os.environ.get(key)
    if not raw:
        return default
    try:
        hh, mm = raw.split(":")
        return time(int(hh), int(mm))
    except (ValueError, TypeError):
        return default


# --- timezone ------------------------------------------------------------
# IST everywhere (CLAUDE.md). Never construct naive datetimes for session logic.
IST = pytz.timezone("Asia/Kolkata")

# --- runtime mode --------------------------------------------------------
# In dev mode the session gate reports OPEN regardless of clock/holiday so the
# scanner can be exercised off-hours. Never enable on the VPS.
INDIA_DEV_MODE: bool = _safe_bool("INDIA_DEV_MODE", False)

# --- regulatory / safety posture ----------------------------------------
# Phase 1 is signal-delivery only. Auto-execution stays hard-off until SEBI RA
# registration + NSE empanelment + owner sign-off (OWNER_BRIEF IB2/IB10).
AUTO_EXECUTION_ENABLED: bool = _safe_bool("AUTO_EXECUTION_ENABLED", False)
INDEX_FUTURES_ONLY: bool = _safe_bool("INDEX_FUTURES_ONLY", True)

# Index futures only at launch (OWNER_BRIEF IB1). Guarded at scanner/expiry entry.
ALLOWED_BASES: tuple[str, ...] = tuple(
    b.strip().upper()
    for b in _safe_str("ALLOWED_BASES", "NIFTY,BANKNIFTY").split(",")
    if b.strip()
)

# --- session clock (IST) -------------------------------------------------
PREOPEN_START: time = _safe_time("INDIA_PREOPEN_START", time(9, 0))
MARKET_OPEN: time = _safe_time("INDIA_MARKET_OPEN", time(9, 15))
LAST_SIGNAL_TIME: time = _safe_time("INDIA_LAST_SIGNAL_TIME", time(15, 20))
FORCE_CLOSE_TIME: time = _safe_time("INDIA_FORCE_CLOSE_TIME", time(15, 25))
MARKET_CLOSE: time = _safe_time("INDIA_MARKET_CLOSE", time(15, 30))
# Expiry-day positions close 5 minutes earlier (Phase 2; OWNER_BRIEF IB16).
EXPIRY_FORCE_CLOSE_TIME: time = _safe_time("INDIA_EXPIRY_FORCE_CLOSE", time(15, 20))

# --- expiry --------------------------------------------------------------
# Weekly index expiry weekday (Mon=0 .. Sun=6); Tuesday per OWNER_BRIEF.
# NOTE (owner): the handover treats NIFTY/BANKNIFTY *futures* as weekly-Tuesday.
# NSE index *futures* are conventionally monthly; weekly cycles are an options
# construct. Flagged for reconciliation — kept configurable in the meantime.
EXPIRY_WEEKDAY: int = _safe_int("INDIA_EXPIRY_WEEKDAY", 1)
# Hour (IST) at/after which an expiry-day contract is treated as rolled to next.
EXPIRY_ROLL_HOUR: int = _safe_int("INDIA_EXPIRY_ROLL_HOUR", 9)


# --- instruments ---------------------------------------------------------
@dataclass(frozen=True)
class Instrument:
    """Static contract metadata for a tradable index base."""

    base: str
    exchange: str
    segment: str
    lot_size: int
    tick_size: float
    expiry_type: str
    min_scalp_points: int


# Lot sizes are NSE-mandated and revised periodically — verify at bootstrap
# (OWNER_BRIEF IB9). Defaults follow the operating docs (NIFTY 75, BANKNIFTY 35);
# the v2 spec's 65/30 is stale. min_scalp_points per OWNER_BRIEF IB11
# (15 NIFTY / 40 BANKNIFTY, covering round-trip STT + brokerage).
INSTRUMENTS: dict[str, Instrument] = {
    "NIFTY": Instrument(
        base="NIFTY",
        exchange="NSE",
        segment="FO",
        lot_size=_safe_int("NIFTY_LOT_SIZE", 75),
        tick_size=_safe_float("NIFTY_TICK_SIZE", 0.05),
        expiry_type="weekly",
        min_scalp_points=_safe_int("NIFTY_MIN_SCALP_POINTS", 15),
    ),
    "BANKNIFTY": Instrument(
        base="BANKNIFTY",
        exchange="NSE",
        segment="FO",
        lot_size=_safe_int("BANKNIFTY_LOT_SIZE", 35),
        tick_size=_safe_float("BANKNIFTY_TICK_SIZE", 0.05),
        expiry_type="weekly",
        min_scalp_points=_safe_int("BANKNIFTY_MIN_SCALP_POINTS", 40),
    ),
}

# --- data files ----------------------------------------------------------
_CONFIG_DIR = Path(__file__).resolve().parent
NSE_HOLIDAYS_FILE: str = _safe_str(
    "NSE_HOLIDAYS_FILE", str(_CONFIG_DIR / "nse_holidays.json")
)
